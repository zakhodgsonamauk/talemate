"""
Tests for the prompt distillation path.

Why this exists: the legacy pipeline assembles the image prompt from keyword lists
and boolean gates that reason over a prompt describing everyone present. That
approach failed in both directions three times - "bare metal walls" read as undress,
a leaked "bare chest" disarmed the nudity negatives, and a garment majority put
`rating_safe` on an explicitly nude scene. Distillation hands the structured scene
facts to one capable model and uses its finished prompt; the legacy pipeline remains
the fallback. Model behaviour itself is covered by scripts/visual_model_bakeoff.py,
not here - these tests cover the plumbing around the call.
"""

from unittest.mock import AsyncMock, patch

import pytest

from talemate.character import Character
from talemate.tale_mate import Scene
from talemate.agents.visual.generation import parse_distilled_response
from talemate.agents.visual.schema import GenerationRequest, VIS_TYPE


KAIRA_ANCHOR = "alien woman, deep violet skin, indigo hair pulled back"
SCENE_ANCHOR = "starship interior, deep space, science fiction, worn metal panelling"

DISTILLED = (
    "PROMPT: 1girl, rating_explicit, alien woman, deep violet skin, indigo hair "
    "pulled back, topless, exposed breasts, leaning over console, starship interior\n"
    "NEGATIVE: 1boy, fully clothed, covered breasts"
)


class _StubClient:
    name = "stub"
    enabled = True
    current_status = "idle"
    decensor_enabled = False
    optimize_prompt_caching = False
    max_token_length = 8192


@pytest.fixture
def agent():
    from talemate.agents.visual.agent import VisualAgent

    kaira = Character(
        name="Kaira",
        visual_anchor=KAIRA_ANCHOR,
        base_attributes={"gender": "female"},
    )
    elmer = Character(name="Elmer", base_attributes={"gender": "male"})

    scene = Scene()
    scene.visual_anchor = SCENE_ANCHOR

    class _Agent(VisualAgent):
        def style_template(self, vis_type):
            return None

    instance = _Agent(client=_StubClient())
    instance.scene = scene
    instance.characters = [kaira, elmer]
    instance.actions["_distillation"].config["enabled"].value = True
    return instance


def _request(prompt: str = None, **kwargs):
    return GenerationRequest(
        prompt=prompt
        or ", ".join([SCENE_ANCHOR, KAIRA_ANCHOR, "Kaira", "control room", "console"]),
        vis_type=kwargs.pop("vis_type", VIS_TYPE.SCENE_ILLUSTRATION),
        **kwargs,
    )


def _stub_llm(response: str):
    return patch(
        "talemate.agents.visual.generation.Prompt.request",
        new=AsyncMock(return_value=(response, {})),
    )


async def _finalize(agent, request):
    from talemate.context import active_scene

    token = active_scene.set(agent.scene)
    try:
        await agent._finalize_prompt(request)
    finally:
        active_scene.reset(token)


# === parse_distilled_response ===


def test_parse_extracts_both_lines():
    positive, negative = parse_distilled_response(DISTILLED)
    assert positive.startswith("1girl, rating_explicit")
    assert negative == "1boy, fully clothed, covered breasts"


def test_parse_tolerates_markdown_dressing():
    positive, negative = parse_distilled_response(
        "**PROMPT:** 1girl, nude\n`NEGATIVE:` 1boy"
    )
    assert positive == "1girl, nude"
    assert negative == "1boy"


def test_parse_survives_a_missing_negative():
    positive, negative = parse_distilled_response("PROMPT: 1girl, console")
    assert positive == "1girl, console"
    assert negative is None


def test_parse_refuses_prose():
    """A response that never states PROMPT: has not followed the contract."""
    positive, negative = parse_distilled_response(
        "Here is a lovely prompt for you:\n1girl, nude, console"
    )
    assert positive is None


def test_parse_takes_the_first_prompt_line_only():
    """Chatty models repeat themselves; the first statement is the answer."""
    positive, _ = parse_distilled_response(
        "PROMPT: 1girl, console\nPROMPT: something else entirely"
    )
    assert positive == "1girl, console"


# === the distillation path ===


async def test_distillation_replaces_prompt_and_negative(agent):
    request = _request()

    with _stub_llm(DISTILLED) as stub:
        await _finalize(agent, request)

    assert stub.call_count == 1
    assert "topless" in request.prompt
    assert "rating_explicit" in request.prompt
    assert "fully clothed" in request.negative_prompt
    assert request.distilled is True


async def test_second_finalize_pass_is_free(agent):
    """FinalizePrompt preview node and generate both run this over one request."""
    request = _request()

    with _stub_llm(DISTILLED) as stub:
        await _finalize(agent, request)
        first = (request.prompt, request.negative_prompt)
        await _finalize(agent, request)

    assert stub.call_count == 1, "the second pass paid a second LLM call"
    assert (request.prompt, request.negative_prompt) == first


async def test_unparseable_response_falls_back_to_legacy(agent):
    """An image with a legacy prompt beats no image."""
    request = _request(
        ", ".join([SCENE_ANCHOR, KAIRA_ANCHOR, "Kaira", "corruption", "console"])
    )

    with _stub_llm("I am sorry, I cannot help with that."):
        await _finalize(agent, request)

    assert request.distilled is False
    # The legacy pipeline ran: its sanitiser drops abstractions like "corruption".
    assert "corruption" not in request.prompt
    assert "console" in request.prompt


async def test_llm_error_falls_back_to_legacy(agent):
    request = _request()

    with patch(
        "talemate.agents.visual.generation.Prompt.request",
        new=AsyncMock(side_effect=RuntimeError("cloud fell over")),
    ):
        await _finalize(agent, request)

    assert request.distilled is False
    assert request.prompt, "fallback still has to produce a prompt"


async def test_toggle_off_never_calls_the_llm(agent):
    agent.actions["_distillation"].config["enabled"].value = False
    request = _request()

    with _stub_llm(DISTILLED) as stub:
        await _finalize(agent, request)

    assert stub.call_count == 0
    assert request.distilled is False


async def test_castless_vis_types_use_the_legacy_path(agent):
    """An object study has no subject to distill around."""
    request = _request(
        "sidearm, workbench, macro detail", vis_type=VIS_TYPE.OBJECT_ILLUSTRATION
    )

    with _stub_llm(DISTILLED) as stub:
        await _finalize(agent, request)

    assert stub.call_count == 0
    assert "sidearm" in request.prompt


async def test_style_keywords_are_reapplied_around_the_distilled_core(agent):
    """apply_styles flattened the style into the old prompt string; the distilled
    prompt replaces that string, so the style is re-fetched from its source."""

    class _Style:
        name = "test style"
        positive_keywords = ["semi-realistic", "cinematic lighting"]
        negative_keywords = ["sketch", "line art"]

    agent.style_template = lambda vis_type: (
        _Style() if vis_type == VIS_TYPE.UNSPECIFIED else None
    )
    request = _request()

    with _stub_llm(DISTILLED):
        await _finalize(agent, request)

    # The profile's full quality chain leads (pony: all four score tags), then
    # the style template's keywords, then the distilled core.
    assert request.prompt.startswith("score_9, score_8_up, score_7_up, score_6_up")
    assert "semi-realistic, cinematic lighting" in request.prompt
    assert "sketch" in request.negative_prompt
    assert "topless" in request.prompt


async def test_distilled_output_is_deduped(agent):
    """The style and the model may repeat each other; the backend must not see both."""

    class _Style:
        name = "test style"
        positive_keywords = ["1girl"]
        negative_keywords = ["1boy"]

    agent.style_template = lambda vis_type: (
        _Style() if vis_type == VIS_TYPE.UNSPECIFIED else None
    )
    request = _request()

    with _stub_llm(DISTILLED):
        await _finalize(agent, request)

    tokens = [t.strip() for t in request.prompt.split(",")]
    assert tokens.count("1girl") == 1
    negative_tokens = [t.strip() for t in request.negative_prompt.split(",")]
    assert negative_tokens.count("1boy") == 1


# === parallel pre-start: distillation overlaps the local keyword write ===


INSTRUCTIONS = "Kaira leans over the console."


async def _begin(agent, **kwargs):
    """begin_prompt_distillation inside scene context, as the template render is."""
    from talemate.context import active_scene

    token = active_scene.set(agent.scene)
    try:
        await agent.begin_prompt_distillation(**kwargs)
    finally:
        active_scene.reset(token)


async def test_prestarted_distillation_is_consumed_not_repeated(agent):
    """The whole point: one cloud call per image, started early."""
    with _stub_llm(DISTILLED) as stub:
        await _begin(
            agent, vis_type=VIS_TYPE.SCENE_ILLUSTRATION, instructions=INSTRUCTIONS
        )
        request = _request(instructions=INSTRUCTIONS)
        await _finalize(agent, request)

    assert stub.call_count == 1, "finalize paid a second distillation call"
    assert request.distilled is True
    assert "topless" in request.prompt
    assert "fully clothed" in request.negative_prompt


async def test_mismatched_pending_task_is_discarded(agent):
    """A pending task for a different ask must not serve the wrong prompt."""
    with _stub_llm(DISTILLED) as stub:
        await _begin(
            agent,
            vis_type=VIS_TYPE.SCENE_ILLUSTRATION,
            instructions="Something else entirely.",
        )
        request = _request(instructions=INSTRUCTIONS)
        await _finalize(agent, request)

    # The mismatched task may or may not have run before cancellation; what is
    # pinned is that finalize distilled fresh for the actual request.
    assert request.distilled is True
    assert stub.call_count >= 1
    assert agent._pending_distillation is None


async def test_prestart_declines_without_subject_evidence(agent):
    """No instructions and no character: subject choice this early would fall
    through to scene order, so the late path (which can read keywords) runs."""
    with _stub_llm(DISTILLED) as stub:
        await _begin(agent, vis_type=VIS_TYPE.SCENE_ILLUSTRATION)

    assert getattr(agent, "_pending_distillation", None) is None
    assert stub.call_count == 0


async def test_prestart_is_a_noop_when_distillation_is_off(agent):
    agent.actions["_distillation"].config["enabled"].value = False

    with _stub_llm(DISTILLED) as stub:
        await _begin(
            agent, vis_type=VIS_TYPE.SCENE_ILLUSTRATION, instructions=INSTRUCTIONS
        )

    assert getattr(agent, "_pending_distillation", None) is None
    assert stub.call_count == 0


async def test_a_failed_pending_task_falls_back_to_a_fresh_distillation(agent):
    from unittest.mock import AsyncMock, patch

    calls = {"n": 0}

    async def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("cloud fell over")
        return (DISTILLED, {})

    with patch(
        "talemate.agents.visual.generation.Prompt.request",
        new=AsyncMock(side_effect=flaky),
    ):
        await _begin(
            agent, vis_type=VIS_TYPE.SCENE_ILLUSTRATION, instructions=INSTRUCTIONS
        )
        request = _request(instructions=INSTRUCTIONS)
        await _finalize(agent, request)

    assert request.distilled is True, "second attempt should have succeeded"
    assert "topless" in request.prompt


async def test_the_template_renders_with_real_scene_objects(agent):
    """The Prompt.request stub means no other test ever renders the jinja - a typo in
    the template would otherwise only be found live."""
    from talemate.prompts import Prompt

    prompt = Prompt.get(
        "visual.distill-image-prompt",
        {
            "scene": agent.scene,
            "recent": ["Kaira entered the engineering bay.", "She got to work."],
            "subject": agent.characters[0],
            "identity": KAIRA_ANCHOR,
            "wardrobe": "fitted utility suit",
            "rules": "",
            "sex": "female",
            "others": "Elmer (male)",
            "scene_anchor": SCENE_ANCHOR,
            "location": "engineering bay",
            "instructions": "Kaira leans over the console.",
            "max_prompt_tokens": 77,
            "decensor": False,
        },
    )
    rendered = prompt.render()

    assert "SUBJECT: Kaira" in rendered
    assert KAIRA_ANCHOR in rendered
    assert "PROMPT:" in rendered
    assert "rating_questionable or rating_explicit" in rendered

async def test_non_auto_shot_mismatches_the_auto_prestart(agent):
    """The prestart cannot see the shot choice and always distills auto; a
    wide-shot finalize must discard it and distill fresh with the shot block."""
    with _stub_llm(DISTILLED) as stub:
        await _begin(
            agent, vis_type=VIS_TYPE.SCENE_ILLUSTRATION, instructions=INSTRUCTIONS
        )
        request = _request(instructions=INSTRUCTIONS)
        request.shot_type = "wide"
        await _finalize(agent, request)

    # The cancelled auto task may or may not have reached the stub before the
    # mismatch; what is pinned is that finalize distilled fresh for the wide ask.
    assert request.distilled is True
    assert agent._pending_distillation is None
    assert stub.call_count >= 1
