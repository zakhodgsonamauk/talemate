"""Tests for checkpoint-aware prompt profiles (model-aware-prompting track)."""

from types import SimpleNamespace

import pytest

from talemate.agents.visual.generation import GenerationMixin
from talemate.agents.visual.schema import (
    GEN_TYPE,
    PROMPT_TYPE,
    PROMPT_PROFILES,
    GenerationRequest,
    get_prompt_profile,
)


class TestBuiltins:
    def test_three_profiles_exist(self):
        assert set(PROMPT_PROFILES) == {"pony", "sdxl_natural", "descriptive"}

    def test_pony_quality_prefix_has_all_four_score_tags(self):
        prefix = PROMPT_PROFILES["pony"].quality_prefix
        for tag in ("score_9", "score_8_up", "score_7_up", "score_6_up"):
            assert tag in prefix
        assert PROMPT_PROFILES["pony"].rating_tags is True
        assert PROMPT_PROFILES["pony"].art_style_required is True

    def test_sdxl_natural_budget_and_conventions(self):
        profile = PROMPT_PROFILES["sdxl_natural"]
        assert profile.max_prompt_tokens == 75
        assert "score" not in profile.quality_prefix.lower()
        assert profile.rating_tags is False
        assert profile.weight_cap == 1.4

    def test_unknown_id_defaults_to_pony(self):
        assert get_prompt_profile("nope").id == "pony"
        assert get_prompt_profile(None).id == "pony"
        assert get_prompt_profile("").id == "pony"


class FakeVisualAgent(GenerationMixin):
    """Just enough agent for resolve_prompt_profile."""

    def __init__(self, model="", overrides="", prompt_type=PROMPT_TYPE.KEYWORDS):
        self._model = model
        self._overrides = overrides
        self.backend = SimpleNamespace(prompt_type=prompt_type)
        self.backend_image_edit = SimpleNamespace(prompt_type=prompt_type)

    def resolve_config(self, action, key):
        if key == "model":
            return self._model
        if key == "checkpoint_profiles":
            return self._overrides
        raise KeyError(key)


class TestResolution:
    def test_pony_checkpoint_by_name(self):
        agent = FakeVisualAgent(model="CyberRealisticPony_V9.0_FP16.safetensors")
        assert agent.resolve_prompt_profile().id == "pony"

    def test_juggernaut_checkpoint_by_name(self):
        agent = FakeVisualAgent(model="Juggernaut-XI-byRunDiffusion.safetensors")
        assert agent.resolve_prompt_profile().id == "sdxl_natural"

    def test_realvis_checkpoint_by_name(self):
        agent = FakeVisualAgent(model="RealVisXL_V5.safetensors")
        assert agent.resolve_prompt_profile().id == "sdxl_natural"

    def test_unknown_checkpoint_defaults_to_pony(self):
        agent = FakeVisualAgent(model="somefinetune_v3.safetensors")
        assert agent.resolve_prompt_profile().id == "pony"

    def test_no_checkpoint_defaults_to_pony(self):
        agent = FakeVisualAgent(model="")
        assert agent.resolve_prompt_profile().id == "pony"

    def test_request_extra_config_overrides_agent_model(self):
        agent = FakeVisualAgent(model="CyberRealisticPony_V9.safetensors")
        request = GenerationRequest(
            prompt="x", extra_config={"checkpoint": "Juggernaut-XII.safetensors"}
        )
        assert agent.resolve_prompt_profile(request).id == "sdxl_natural"

    def test_explicit_checkpoint_arg_wins(self):
        agent = FakeVisualAgent(model="CyberRealisticPony_V9.safetensors")
        assert (
            agent.resolve_prompt_profile(checkpoint="juggernaut_x.safetensors").id
            == "sdxl_natural"
        )

    def test_config_override_beats_patterns(self):
        agent = FakeVisualAgent(
            model="Juggernaut-XI.safetensors",
            overrides='{"juggernaut": "pony"}',
        )
        assert agent.resolve_prompt_profile().id == "pony"

    def test_bad_override_json_falls_back_to_patterns(self):
        agent = FakeVisualAgent(
            model="Juggernaut-XI.safetensors", overrides="{not json"
        )
        assert agent.resolve_prompt_profile().id == "sdxl_natural"

    def test_descriptive_backend_wins_over_checkpoint(self):
        agent = FakeVisualAgent(
            model="CyberRealisticPony_V9.safetensors",
            prompt_type=PROMPT_TYPE.DESCRIPTIVE,
        )
        assert agent.resolve_prompt_profile().id == "descriptive"

    def test_request_pinned_profile_wins(self):
        agent = FakeVisualAgent(model="CyberRealisticPony_V9.safetensors")
        request = GenerationRequest(prompt="x", prompt_profile="sdxl_natural")
        assert agent.resolve_prompt_profile(request).id == "sdxl_natural"
