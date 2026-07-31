"""Unit tests for director narration supervision gating.

Covers:
- New supervise_narration configs (frequency, precheck_client) exist with
  correct defaults and their property helpers resolve them.
- Frequency gate: passes every narration at 1, every Nth at N.
- Handler: gated narrations skip supervision entirely; the pre-check
  triage on a separate client short-circuits the full review on PASS,
  escalates on ISSUES, fails open on ambiguous verdicts and unknown
  client names; author mode bypasses the pre-check.
"""

from __future__ import annotations

import pytest

from conftest import MockClient, MockScene, bootstrap_scene

import talemate.instance as instance
from _director_test_helpers import patch_prompt_request_in
from talemate.agents.narrator import NarratorAgentEmission
from talemate.prompts.base import Prompt

PRECHECK_TEMPLATE = "director.supervise-narration-precheck"
FULL_TEMPLATE = "director.supervise-narration"


@pytest.fixture
def scene():
    s = MockScene()
    bootstrap_scene(s)
    return s


@pytest.fixture
def director(scene):
    director = instance.get_agent("director")
    director.actions["supervise_narration"].enabled = True
    yield director
    director.actions["supervise_narration"].enabled = False


class FastMockClient(MockClient):
    """MockClient with a settable max_token_length (property on ClientBase)."""

    max_token_length = 8192


@pytest.fixture
def fast_client():
    client = FastMockClient("FastTest")
    instance.CLIENTS["FastTest"] = client
    yield client
    instance.CLIENTS.pop("FastTest", None)


def _set_config(director, key, value):
    director.actions["supervise_narration"].config[key].value = value


def _emission(scene, response="The corridor hummed with neon light."):
    narrator = instance.get_agent("narrator")
    return NarratorAgentEmission(agent=narrator, response=response)


class TestPrecheckTemplate:
    def test_renders(self, director, scene):
        prompt = Prompt.get(
            PRECHECK_TEMPLATE,
            vars={
                "scene": scene,
                "max_tokens": 8192,
                "draft": "The corridor hummed with neon light.",
                "guidance": "Watch for purple prose.",
            },
        )
        rendered = prompt.render()
        assert "The corridor hummed with neon light." in rendered
        assert "PASS" in rendered
        assert "ISSUES" in rendered
        assert "Watch for purple prose." in rendered


class TestConfigDefaults:
    def test_configs_exist_with_defaults(self, director):
        config = director.actions["supervise_narration"].config
        assert config["frequency"].value == 1
        assert config["precheck_client"].value == ""

    def test_property_helpers(self, director):
        assert director.narration_supervision_frequency == 1
        assert director.narration_supervision_precheck_client == ""
        _set_config(director, "frequency", 3)
        _set_config(director, "precheck_client", "  FastTest  ")
        assert director.narration_supervision_frequency == 3
        assert director.narration_supervision_precheck_client == "FastTest"


class TestFrequencyGate:
    def test_default_passes_every_narration(self, director):
        assert all(
            director._narration_supervision_frequency_gate() for _ in range(5)
        )

    def test_every_second_narration(self, director):
        _set_config(director, "frequency", 2)
        results = [
            director._narration_supervision_frequency_gate() for _ in range(6)
        ]
        assert results == [False, True, False, True, False, True]

    def test_every_third_narration(self, director):
        _set_config(director, "frequency", 3)
        results = [
            director._narration_supervision_frequency_gate() for _ in range(6)
        ]
        assert results == [False, False, True, False, False, True]

    @pytest.mark.asyncio
    async def test_gated_narration_skips_supervision(
        self, director, scene, monkeypatch
    ):
        _set_config(director, "frequency", 2)
        calls = []

        async def fake_process(draft):
            calls.append(draft)
            return None

        monkeypatch.setattr(
            director, "narration_supervision_process", fake_process
        )
        await director.narration_supervision_on_narrator_generated(
            _emission(scene)
        )
        await director.narration_supervision_on_narrator_generated(
            _emission(scene)
        )
        assert len(calls) == 1


class TestPrecheck:
    @pytest.mark.asyncio
    async def test_no_precheck_client_runs_full_review(
        self, director, scene, monkeypatch
    ):
        stub = patch_prompt_request_in(monkeypatch)(
            {FULL_TEMPLATE: [("PASS", {})]}
        )
        await director.narration_supervision_on_narrator_generated(
            _emission(scene)
        )
        templates = [c["template"] for c in stub.calls]
        assert templates == [FULL_TEMPLATE]

    @pytest.mark.asyncio
    async def test_precheck_pass_skips_full_review(
        self, director, scene, fast_client, monkeypatch
    ):
        _set_config(director, "precheck_client", "FastTest")
        stub = patch_prompt_request_in(monkeypatch)(
            {PRECHECK_TEMPLATE: [("PASS", {})]}
        )
        emission = _emission(scene)
        original = emission.response
        await director.narration_supervision_on_narrator_generated(emission)
        templates = [c["template"] for c in stub.calls]
        assert templates == [PRECHECK_TEMPLATE]
        assert emission.response == original

    @pytest.mark.asyncio
    async def test_precheck_issues_escalates_to_full_review(
        self, director, scene, fast_client, monkeypatch
    ):
        _set_config(director, "precheck_client", "FastTest")
        stub = patch_prompt_request_in(monkeypatch)(
            {
                PRECHECK_TEMPLATE: [("ISSUES", {})],
                FULL_TEMPLATE: [
                    ("<NARRATION>Something actually new happens.</NARRATION>", {})
                ],
            }
        )
        emission = _emission(scene)
        await director.narration_supervision_on_narrator_generated(emission)
        templates = [c["template"] for c in stub.calls]
        assert templates == [PRECHECK_TEMPLATE, FULL_TEMPLATE]
        assert emission.response == "Something actually new happens."

    @pytest.mark.asyncio
    async def test_precheck_uses_configured_client(
        self, director, scene, fast_client, monkeypatch
    ):
        _set_config(director, "precheck_client", "FastTest")
        stub = patch_prompt_request_in(monkeypatch)(
            {PRECHECK_TEMPLATE: [("PASS", {})]}
        )
        await director.narration_supervision_on_narrator_generated(
            _emission(scene)
        )
        precheck_call = stub.calls[0]
        assert precheck_call["kind"] == "investigate_16"
        assert precheck_call["vars"]["max_tokens"] == 8192

    @pytest.mark.asyncio
    async def test_ambiguous_verdict_fails_open(
        self, director, scene, fast_client, monkeypatch
    ):
        """A refusal or garbage verdict escalates to the full review."""
        _set_config(director, "precheck_client", "FastTest")
        stub = patch_prompt_request_in(monkeypatch)(
            {
                PRECHECK_TEMPLATE: [("I cannot assist with that request.", {})],
                FULL_TEMPLATE: [("PASS", {})],
            }
        )
        await director.narration_supervision_on_narrator_generated(
            _emission(scene)
        )
        templates = [c["template"] for c in stub.calls]
        assert templates == [PRECHECK_TEMPLATE, FULL_TEMPLATE]

    @pytest.mark.asyncio
    async def test_unknown_client_fails_open(self, director, scene, monkeypatch):
        _set_config(director, "precheck_client", "DoesNotExist")
        stub = patch_prompt_request_in(monkeypatch)(
            {FULL_TEMPLATE: [("PASS", {})]}
        )
        await director.narration_supervision_on_narrator_generated(
            _emission(scene)
        )
        templates = [c["template"] for c in stub.calls]
        assert templates == [FULL_TEMPLATE]

    @pytest.mark.asyncio
    async def test_author_mode_bypasses_precheck(
        self, director, scene, fast_client, monkeypatch
    ):
        _set_config(director, "precheck_client", "FastTest")
        _set_config(director, "mode", "author")
        try:
            stub = patch_prompt_request_in(monkeypatch)(
                {
                    FULL_TEMPLATE: [
                        ("<NARRATION>Authored narration.</NARRATION>", {})
                    ]
                }
            )
            emission = _emission(scene)
            await director.narration_supervision_on_narrator_generated(emission)
            templates = [c["template"] for c in stub.calls]
            assert templates == [FULL_TEMPLATE]
            assert emission.response == "Authored narration."
        finally:
            _set_config(director, "mode", "supervise")
