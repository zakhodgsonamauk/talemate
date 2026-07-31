"""VRAM handoff between the local text model and the image backend.

One consumer GPU cannot hold the Ollama text model and ComfyUI's checkpoint at the
same time (measured on a 16GB RTX 4080: ~8.6GB text + ~11GB SDXL with IP-Adapter).
The static `--reserve-vram` split leaves the image side paging weights over PCIe on
every generation. The handoff instead gives each side the whole card: unload the
text model before the image generates, load it back after.

Unloading is cheap and safe — Ollama drops the weights on `keep_alive: 0` and
reloads them on the next request regardless, from the OS file cache when RAM
allows. Opt-in via the visual agent's `vram_handoff` toggle because on a machine
with VRAM to spare the unload is pure cost.
"""

import asyncio

import httpx
import structlog

from talemate.instance import client_instances

log = structlog.get_logger("talemate.agents.visual.vram")

# Listing and unloading are trivial requests; only re-warm actually loads weights,
# and Talemate's next text generation would wait on that load anyway.
REQUEST_TIMEOUT = 30


async def loaded_gpu_models(api_url: str, *, transport=None) -> list[str]:
    """Names of models this Ollama server currently holds in GPU memory.

    CPU-resident models are excluded: unloading them frees no VRAM and re-warming
    them later would be pointless churn.
    """
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.get(f"{api_url}/api/ps", timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        models = response.json().get("models", [])

    return [
        entry.get("model") or entry.get("name")
        for entry in models
        if entry.get("size_vram", 0) > 0
    ]


async def unload_models(api_url: str, *, transport=None) -> list[str]:
    """Unload every GPU-resident model, returning the names actually unloaded.

    Never raises: an unreachable server or a model that refuses to unload leaves
    the generation no worse off than without the handoff.
    """
    try:
        models = await loaded_gpu_models(api_url, transport=transport)
    except Exception as e:
        log.warning("vram_handoff.list_failed", api_url=api_url, error=str(e))
        return []

    unloaded: list[str] = []
    async with httpx.AsyncClient(transport=transport) as client:
        for model in models:
            try:
                response = await client.post(
                    f"{api_url}/api/generate",
                    json={"model": model, "keep_alive": 0},
                    timeout=REQUEST_TIMEOUT,
                )
                response.raise_for_status()
                unloaded.append(model)
            except Exception as e:
                log.warning(
                    "vram_handoff.unload_failed",
                    api_url=api_url,
                    model=model,
                    error=str(e),
                )

    if unloaded:
        log.info("vram_handoff.unloaded", api_url=api_url, models=unloaded)
    return unloaded


async def rewarm_models(api_url: str, models: list[str], *, transport=None) -> None:
    """Load models back into memory.

    A bare /api/generate with no prompt loads the model under the server's default
    keep_alive. No keep_alive is sent — 0 would immediately unload it again.
    """
    async with httpx.AsyncClient(transport=transport) as client:
        for model in models:
            try:
                response = await client.post(
                    f"{api_url}/api/generate",
                    json={"model": model},
                    timeout=REQUEST_TIMEOUT,
                )
                response.raise_for_status()
                log.info("vram_handoff.rewarmed", api_url=api_url, model=model)
            except Exception as e:
                # The next text generation will load it anyway, just slower.
                log.warning(
                    "vram_handoff.rewarm_failed",
                    api_url=api_url,
                    model=model,
                    error=str(e),
                )


class VRAMHandoffMixin:
    """Wraps a backend generation with the unload / re-warm pair."""

    @property
    def vram_handoff_enabled(self) -> bool:
        try:
            return bool(self.actions["_config"].config["vram_handoff"].value)
        except (KeyError, AttributeError):
            return False

    def _ollama_api_urls(self) -> list[str]:
        urls: list[str] = []
        for _name, client in client_instances():
            if getattr(client, "client_type", None) != "ollama":
                continue
            if not getattr(client, "enabled", True):
                continue
            api_url = getattr(client, "api_url", None)
            if api_url and api_url not in urls:
                urls.append(api_url)
        return urls

    def _other_generations_active(self) -> bool:
        tasks = getattr(self, "_active_generation_tasks", None) or set()
        current = asyncio.current_task()
        return any(not task.done() for task in tasks if task is not current)

    async def release_text_model_vram(self) -> None:
        if not self.vram_handoff_enabled:
            return

        unloaded: dict[str, list[str]] = getattr(self, "_vram_unloaded", None) or {}
        for api_url in self._ollama_api_urls():
            models = await unload_models(api_url)
            if models:
                merged = unloaded.setdefault(api_url, [])
                merged.extend(m for m in models if m not in merged)
        self._vram_unloaded = unloaded

    async def restore_text_model_vram(self) -> None:
        unloaded: dict[str, list[str]] = getattr(self, "_vram_unloaded", None) or {}
        if not unloaded:
            return

        # Another image is still generating; it needs the VRAM more than the text
        # model does. Its own restore will re-warm from the same accumulated state.
        if self._other_generations_active():
            log.debug("vram_handoff.restore_deferred", reason="generation active")
            return

        self._vram_unloaded = {}
        for api_url, models in unloaded.items():
            await rewarm_models(api_url, models)

    async def _backend_generate(self, backend, request, response):
        """Run a backend generation inside the handoff.

        The re-warm sits in `finally`: a failed generation must not leave the text
        model unloaded, or the next story turn stalls on a cold load mid-scene.
        """
        await self.release_text_model_vram()
        try:
            return await backend.generate(request, response)
        finally:
            await self.restore_text_model_vram()
