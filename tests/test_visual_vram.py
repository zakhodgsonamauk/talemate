"""Tests for the VRAM handoff — freeing the local text model's GPU memory while an
image generates, then loading it back.

One RTX 4080 has to hold both the Ollama text model (~8.6GB) and ComfyUI's checkpoint
(~11GB with SDXL + IP-Adapter). They do not fit together: the static `--reserve-vram`
split leaves the image model paging weights over PCIe on every generation. The handoff
trades a few seconds of model reload for each side getting the whole card.

Opt-in via the visual agent's `vram_handoff` config toggle, because on a multi-GPU or
big-VRAM machine unloading the text model is pure cost.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from talemate.agents.visual.vram import (
    VRAMHandoffMixin,
    loaded_gpu_models,
    rewarm_models,
    unload_models,
)


API_URL = "http://localhost:11434"


def _ollama_transport(recorded: list, ps_models: list[dict], fail_unload: set = ()):
    """A MockTransport speaking just enough of the Ollama API for these tests.

    Records every request as (method, path, payload). `/api/ps` returns `ps_models`;
    `/api/generate` 500s for models named in `fail_unload`.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content) if request.content else None
        recorded.append((request.method, request.url.path, payload))

        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": ps_models})
        if request.url.path == "/api/generate":
            if payload and payload.get("model") in fail_unload:
                return httpx.Response(500, json={"error": "boom"})
            return httpx.Response(200, json={"done": True})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


ROCINANTE = {"model": "rocinante:12b", "size_vram": 8_600_000_000}
QWEN_CPU = {"model": "qwen3-embedding:4b-cpu", "size_vram": 0}
MISTRAL = {"model": "mistral:7b", "size_vram": 4_000_000_000}


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loaded_gpu_models_ignores_cpu_resident_models():
    recorded = []
    transport = _ollama_transport(recorded, [ROCINANTE, QWEN_CPU, MISTRAL])

    models = await loaded_gpu_models(API_URL, transport=transport)

    assert models == ["rocinante:12b", "mistral:7b"]


@pytest.mark.asyncio
async def test_unload_models_sends_keep_alive_zero_per_gpu_model():
    recorded = []
    transport = _ollama_transport(recorded, [ROCINANTE, MISTRAL])

    unloaded = await unload_models(API_URL, transport=transport)

    assert unloaded == ["rocinante:12b", "mistral:7b"]
    unload_calls = [r for r in recorded if r[1] == "/api/generate"]
    assert [c[2]["model"] for c in unload_calls] == ["rocinante:12b", "mistral:7b"]
    assert all(c[2]["keep_alive"] == 0 for c in unload_calls)


@pytest.mark.asyncio
async def test_unload_models_survives_one_model_failing():
    """A model that refuses to unload must not stop the others, and must not be
    reported as unloaded — re-warming it later would be wrong."""
    recorded = []
    transport = _ollama_transport(
        recorded, [ROCINANTE, MISTRAL], fail_unload={"rocinante:12b"}
    )

    unloaded = await unload_models(API_URL, transport=transport)

    assert unloaded == ["mistral:7b"]


@pytest.mark.asyncio
async def test_unload_models_returns_empty_when_server_unreachable():
    """Ollama not running is a normal state (cloud-only text), never an error."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    unloaded = await unload_models(
        API_URL, transport=httpx.MockTransport(handler)
    )

    assert unloaded == []


@pytest.mark.asyncio
async def test_rewarm_models_posts_generate_without_prompt():
    """An empty /api/generate loads the model with the server's default keep_alive.
    Sending keep_alive: 0 here would immediately unload it again."""
    recorded = []
    transport = _ollama_transport(recorded, [])

    await rewarm_models(API_URL, ["rocinante:12b"], transport=transport)

    calls = [r for r in recorded if r[1] == "/api/generate"]
    assert [c[2]["model"] for c in calls] == ["rocinante:12b"]
    assert all("keep_alive" not in c[2] for c in calls)
    assert all("prompt" not in c[2] for c in calls)


# ---------------------------------------------------------------------------
# Mixin orchestration
# ---------------------------------------------------------------------------


def _toggle(value: bool):
    return {
        "_config": SimpleNamespace(
            config={"vram_handoff": SimpleNamespace(value=value)}
        )
    }


class FakeAgent(VRAMHandoffMixin):
    def __init__(self, enabled: bool = True):
        self.actions = _toggle(enabled)


def _ollama_client(api_url: str = API_URL, enabled: bool = True):
    return SimpleNamespace(client_type="ollama", enabled=enabled, api_url=api_url)


@pytest.mark.asyncio
async def test_release_unloads_each_enabled_ollama_client_url():
    agent = FakeAgent(enabled=True)
    clients = [
        ("a", _ollama_client("http://localhost:11434")),
        ("b", _ollama_client("http://localhost:11434")),  # same URL: once only
        ("c", _ollama_client("http://other:11434", enabled=False)),
        ("d", SimpleNamespace(client_type="openai", enabled=True)),
    ]

    with (
        patch(
            "talemate.agents.visual.vram.client_instances", return_value=clients
        ),
        patch(
            "talemate.agents.visual.vram.unload_models",
            new=AsyncMock(return_value=["rocinante:12b"]),
        ) as unload,
    ):
        await agent.release_text_model_vram()

    unload.assert_awaited_once_with("http://localhost:11434")


@pytest.mark.asyncio
async def test_release_is_noop_when_toggle_off():
    agent = FakeAgent(enabled=False)

    with patch(
        "talemate.agents.visual.vram.unload_models", new=AsyncMock()
    ) as unload:
        await agent.release_text_model_vram()
        await agent.restore_text_model_vram()

    unload.assert_not_awaited()


@pytest.mark.asyncio
async def test_restore_rewarns_what_release_unloaded_then_clears():
    agent = FakeAgent(enabled=True)
    clients = [("a", _ollama_client())]

    with (
        patch(
            "talemate.agents.visual.vram.client_instances", return_value=clients
        ),
        patch(
            "talemate.agents.visual.vram.unload_models",
            new=AsyncMock(return_value=["rocinante:12b"]),
        ),
        patch(
            "talemate.agents.visual.vram.rewarm_models", new=AsyncMock()
        ) as rewarm,
    ):
        await agent.release_text_model_vram()
        await agent.restore_text_model_vram()
        # Second restore has nothing left to do.
        await agent.restore_text_model_vram()

    rewarm.assert_awaited_once_with(API_URL, ["rocinante:12b"])


@pytest.mark.asyncio
async def test_restore_waits_for_other_active_generations():
    """Two images generating back-to-back: the first to finish must not reload the
    text model into VRAM the second one is still using."""
    agent = FakeAgent(enabled=True)

    async def hang():
        await asyncio.sleep(30)

    other = asyncio.get_event_loop().create_task(hang())
    agent._active_generation_tasks = {other}

    agent._vram_unloaded = {API_URL: ["rocinante:12b"]}
    with patch(
        "talemate.agents.visual.vram.rewarm_models", new=AsyncMock()
    ) as rewarm:
        await agent.restore_text_model_vram()
        rewarm.assert_not_awaited()

        # State must survive the skip so the last generation's restore re-warms.
        other.cancel()
        agent._active_generation_tasks = set()
        await agent.restore_text_model_vram()
        rewarm.assert_awaited_once_with(API_URL, ["rocinante:12b"])


@pytest.mark.asyncio
async def test_backend_generate_releases_before_and_restores_after():
    agent = FakeAgent(enabled=True)
    order = []

    async def release():
        order.append("release")

    async def restore():
        order.append("restore")

    async def generate(request, response):
        order.append("generate")
        return response

    agent.release_text_model_vram = release
    agent.restore_text_model_vram = restore
    backend = SimpleNamespace(generate=generate)

    result = await agent._backend_generate(backend, "req", "resp")

    assert result == "resp"
    assert order == ["release", "generate", "restore"]


@pytest.mark.asyncio
async def test_backend_generate_restores_when_generation_fails():
    agent = FakeAgent(enabled=True)
    restored = []

    async def release():
        pass

    async def restore():
        restored.append(True)

    async def generate(request, response):
        raise RuntimeError("comfyui exploded")

    agent.release_text_model_vram = release
    agent.restore_text_model_vram = restore
    backend = SimpleNamespace(generate=generate)

    with pytest.raises(RuntimeError):
        await agent._backend_generate(backend, "req", "resp")

    assert restored == [True]


# ---------------------------------------------------------------------------
# Wiring into the real agent
# ---------------------------------------------------------------------------


def test_visual_agent_has_vram_handoff_toggle_and_mixin():
    from talemate.agents.visual.agent import VisualAgent

    assert issubclass(VisualAgent, VRAMHandoffMixin)
    actions = VisualAgent.init_actions()
    assert "vram_handoff" in actions["_config"].config


@pytest.mark.asyncio
async def test_generate_text_to_image_routes_through_handoff():
    """The generation path must call _backend_generate, not backend.generate
    directly — otherwise the toggle exists but does nothing."""
    from talemate.agents.visual.generation import GenerationMixin
    from talemate.agents.visual.schema import BackendStatusType

    class WiredAgent(GenerationMixin, VRAMHandoffMixin):
        def __init__(self, backend):
            self.backend = backend
            self.actions = _toggle(True)

        async def set_background_processing(self, task, on_error):
            await task

    calls = []

    async def generate(request, response):
        calls.append("backend.generate")
        return response

    backend = SimpleNamespace(
        name="fake",
        status=SimpleNamespace(type=BackendStatusType.OK),
        generate=generate,
    )
    agent = WiredAgent(backend)

    handoff_calls = []

    async def fake_backend_generate(backend_arg, request, response):
        handoff_calls.append("handoff")
        return await backend_arg.generate(request, response)

    agent._backend_generate = fake_backend_generate

    request = SimpleNamespace(gen_type=None)
    response = SimpleNamespace()

    async def on_done(fut):
        pass

    await agent.generate_text_to_image(request, response, on_done)
    # generation runs as a tracked background task; give it a tick
    for _ in range(5):
        await asyncio.sleep(0)

    assert handoff_calls == ["handoff"]
    assert calls == ["backend.generate"]
