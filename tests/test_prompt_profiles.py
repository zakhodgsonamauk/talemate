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

    def _max_prompt_tokens(self):
        return 150


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


class TestLegacyPathGuard:
    @pytest.mark.asyncio
    async def test_sdxl_natural_legacy_strips_pony_tags(self):
        agent = FakeVisualAgent(model="Juggernaut-XI.safetensors")
        request = GenerationRequest(
            prompt="score_9, score_8_up, rating_safe, source_anime, 1girl, violet skin, standing at console",
            prompt_profile="sdxl_natural",
        )
        await agent._finalize_prompt(request)
        low = (request.prompt or "").lower()
        assert "score_" not in low
        assert "rating_" not in low
        assert "source_" not in low
        assert "violet skin" in low

    @pytest.mark.asyncio
    async def test_pony_legacy_keeps_tag_machinery(self):
        agent = FakeVisualAgent(model="CyberRealisticPony_V9.safetensors")
        request = GenerationRequest(
            prompt="score_9, 1girl, violet skin, standing at console",
            prompt_profile="pony",
        )
        await agent._finalize_prompt(request)
        assert "score_9" in (request.prompt or "")


class TestDistillTemplate:
    def _render(self, profile_id, shot_type="auto"):
        import jinja2
        from talemate.agents.visual.generation import SHOT_BLOCKS
        from talemate.agents.visual.schema import PROMPT_PROFILES

        src = open(
            "src/talemate/prompts/templates/visual/distill-image-prompt.jinja2",
            encoding="utf-8",
        ).read()
        env = jinja2.Environment()
        env.globals["llm_can_be_coerced"] = lambda: False
        env.globals["set_prepared_response"] = lambda *_a, **_k: ""
        profile = PROMPT_PROFILES[profile_id]
        return env.from_string(src).render(
            subject=SimpleNamespace(name="Kaira"),
            identity="violet skin, indigo hair",
            wardrobe="",
            rules="",
            sex="female",
            others="",
            scene_anchor="starship corridor",
            location="",
            instructions="Kaira draws her pistol.",
            recent=[],
            max_prompt_tokens=profile.max_prompt_tokens,
            dialect=profile.dialect_instructions,
            profile_id=profile.id,
            shot=SHOT_BLOCKS.get(shot_type, ""),
        )

    def test_pony_render_has_four_section_contract(self):
        text = self._render("pony")
        assert "Pony structure" in text
        assert "rating_safe" in text
        assert "booster tags" in text.lower()
        assert "HARD CAP" not in text

    def test_sdxl_render_has_natural_contract(self):
        text = self._render("sdxl_natural")
        assert "HARD CAP" in text
        assert "75 tokens" in text
        assert "Pony structure" not in text
        assert "Do NOT use score_9" in text

    # The shot block is dialect-neutral: the same framing contract must land in
    # both dialects, and auto must inject nothing.
    @pytest.mark.parametrize("profile_id", ["pony", "sdxl_natural"])
    def test_wide_shot_block_renders(self, profile_id):
        text = self._render(profile_id, shot_type="wide")
        assert "WIDE SHOT" in text
        assert "wide establishing shot" in text
        assert "lone" in text and "small in frame" in text
        # negatives that keep the checkpoint off the hero pose
        for term in ("close-up", "portrait", "looking at viewer"):
            assert term in text

    @pytest.mark.parametrize("profile_id", ["pony", "sdxl_natural"])
    def test_closeup_shot_block_renders(self, profile_id):
        text = self._render(profile_id, shot_type="closeup")
        assert "CLOSE-UP" in text
        assert "detailed face" in text
        assert "wide shot" in text  # in the NEGATIVE instruction

    @pytest.mark.parametrize("profile_id", ["pony", "sdxl_natural"])
    def test_auto_shot_injects_nothing(self, profile_id):
        text = self._render(profile_id, shot_type="auto")
        assert "REQUESTED FRAMING" not in text

    def test_medium_shot_block_renders(self):
        text = self._render("pony", shot_type="medium")
        assert "MEDIUM SHOT" in text
        assert "full body" in text


class TestClipSkip:
    def test_pony_profile_carries_clip_skip(self):
        assert PROMPT_PROFILES["pony"].clip_skip == -2
        assert PROMPT_PROFILES["sdxl_natural"].clip_skip == -1
        assert PROMPT_PROFILES["descriptive"].clip_skip == -1

    def test_workflows_carry_the_clip_skip_node(self):
        import json

        for f in (
            "default-sdxl.json",
            "sdxl-ipadapter-character.json",
            "sdxl-ipadapter-character-multi.json",
            "sdxl-ipadapter-inpaint.json",
        ):
            d = json.load(
                open(f"templates/comfyui-workflows/{f}", encoding="utf-8")
            )
            graph = d.get("prompt", d)
            skips = [
                n for n in graph.values() if n.get("class_type") == "CLIPSetLastLayer"
            ]
            assert len(skips) == 1, f
            # both text encoders read the skipped clip
            for nid in ("4", "5"):
                assert graph[nid]["inputs"]["clip"][0] == "90", f

    def test_workflow_set_clip_skip(self):
        from talemate.agents.visual.backends.comfyui import Workflow

        import json

        raw = json.load(
            open(
                "templates/comfyui-workflows/sdxl-ipadapter-character.json",
                encoding="utf-8",
            )
        )
        workflow = Workflow(
            nodes=raw.get("prompt", raw), mtime=0.0, path="test"
        )
        workflow.set_clip_skip(-2)
        skip_nodes = [
            n
            for n in workflow.nodes.values()
            if n.get("class_type") == "CLIPSetLastLayer"
        ]
        assert skip_nodes and all(
            n["inputs"]["stop_at_clip_layer"] == -2 for n in skip_nodes
        )
