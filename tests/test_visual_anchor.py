"""Tests for visual anchors — the deterministic character/scene appearance tokens
that keep generated illustrations consistent.

See docs/fork/visual-consistency-design.md. The short version: appearance used to be
re-derived by the LLM on every generation, so the subject could not be stable. An
anchor is derived once from canonical prose, cached on the character, and injected
verbatim from then on.
"""

from unittest.mock import AsyncMock, patch

import pytest

from talemate.character import Character
from talemate.tale_mate import Scene


KAIRA_APPEARANCE = (
    "Just over six feet, lean and angular. Deep violet skin with faint geometric "
    "patterns along her forearms and jaw -- natural Altrusian markings, not "
    "decorative. Large dark eyes, no visible iris. Hair is a deep indigo, worn "
    "pulled back and secured. Four-fingered hands. Wears a fitted utility suit in "
    "dark blue-grey, pockets and tool loops on the belt."
)


@pytest.fixture
def kaira():
    return Character(
        name="Kaira",
        description="Altrusian first officer of the Starlight Nomad.",
        base_attributes={
            "gender": "female",
            "species": "Altrusian",
            "age": "37",
            "appearance": KAIRA_APPEARANCE,
        },
    )


# ---------------------------------------------------------------------------
# T1 — Character.visual_anchor
# ---------------------------------------------------------------------------


def test_character_visual_anchor_defaults_to_none(kaira):
    assert kaira.visual_anchor is None


def test_character_visual_anchor_round_trips(kaira):
    # Identity only. Clothing in a visual_anchor is now stripped on construction - see
    # the freshness track's migration tests.
    kaira.visual_anchor = "violet skin, indigo hair, four-fingered hands"

    restored = Character(**kaira.model_dump())

    assert restored.visual_anchor == kaira.visual_anchor


def test_character_visual_anchor_is_serialized(kaira):
    """The field has to reach the scene JSON or nothing is cached between runs."""
    assert "visual_anchor" in kaira.model_dump()


# ---------------------------------------------------------------------------
# T2 — Scene.visual_anchor
# ---------------------------------------------------------------------------


def test_scene_visual_anchor_defaults_to_none():
    scene = Scene()
    assert scene.visual_anchor is None


def test_scene_visual_anchor_survives_serialize():
    scene = Scene()
    scene.visual_anchor = "starship interior, deep space, sci-fi, worn metal panels"

    assert scene.serialize["visual_anchor"] == scene.visual_anchor


# ---------------------------------------------------------------------------
# T4 — derive and cache
# ---------------------------------------------------------------------------


DERIVED = (
    "alien woman, deep violet skin, geometric facial markings, indigo hair pulled "
    "back, large black eyes, lean and tall, four-fingered hands, fitted dark "
    "blue-grey utility suit"
)


class _StubClient:
    """Just enough client for the prompt machinery; Prompt.request is stubbed anyway."""

    name = "stub"
    enabled = True
    current_status = "idle"
    decensor_enabled = False
    optimize_prompt_caching = False
    max_token_length = 8192


@pytest.fixture
def anchor_agent():
    """
    A real VisualAgent, not a hand-rolled double.

    The first version of this fixture was a bare class carrying only AnchorMixin, and it
    passed while the real thing raised AttributeError from a websocket handler: the
    prompt machinery reads an ActiveAgent context that set_processing establishes, and a
    stub agent never exercised it. Use the real class.
    """
    from talemate.agents.visual.agent import VisualAgent

    return VisualAgent(client=_StubClient())


def _stub_request(anchor_text: str):
    """Patch Prompt.request to return an <ANCHOR> payload without touching an LLM."""
    return patch(
        "talemate.agents.visual.anchors.Prompt.request",
        new=AsyncMock(return_value=("raw", {"anchor": anchor_text})),
    )


async def test_character_anchor_uses_cached_value_without_calling_the_llm(
    kaira, anchor_agent
):
    """The whole point of caching. A second image must not re-derive appearance."""
    kaira.visual_anchor = DERIVED

    with _stub_request("SHOULD NOT BE USED") as stub:
        result = await anchor_agent.character_anchor(kaira)

    assert result == DERIVED
    assert stub.call_count == 0


async def test_character_anchor_derives_and_writes_back(kaira, anchor_agent):
    with _stub_request(DERIVED) as stub:
        result = await anchor_agent.character_anchor(kaira)

    assert result == DERIVED
    assert kaira.visual_anchor == DERIVED, "derived anchor was not cached"
    assert stub.call_count == 1


async def test_character_anchor_second_call_hits_the_cache(kaira, anchor_agent):
    with _stub_request(DERIVED) as stub:
        await anchor_agent.character_anchor(kaira)
        await anchor_agent.character_anchor(kaira)

    assert stub.call_count == 1


async def test_character_anchor_strips_wrapper_and_normalises(kaira, anchor_agent):
    messy = "  <ANCHOR>alien woman,deep violet skin ,  indigo hair,alien woman</ANCHOR>\n"

    with _stub_request(messy):
        result = await anchor_agent.character_anchor(kaira)

    assert result == "alien woman, deep violet skin, indigo hair"


async def test_character_anchor_returns_none_without_source_material(anchor_agent):
    blank = Character(name="Ghost")

    with _stub_request(DERIVED) as stub:
        result = await anchor_agent.character_anchor(blank)

    assert result is None
    assert stub.call_count == 0, "no source material means no point asking the LLM"


async def test_character_anchor_survives_an_empty_llm_response(kaira, anchor_agent):
    with _stub_request("   "):
        result = await anchor_agent.character_anchor(kaira)

    assert result is None
    assert kaira.visual_anchor is None, "an empty derivation must not be cached"


async def test_scene_anchor_derives_and_writes_back(anchor_agent):
    scene = Scene()
    scene.description = "The Starlight Nomad, a deep-space survey vessel."
    derived = "starship interior, deep space, science fiction, worn metal panelling"

    with _stub_request(derived) as stub:
        result = await anchor_agent.scene_anchor(scene)

    assert result == derived
    assert scene.visual_anchor == derived
    assert stub.call_count == 1


async def test_scene_anchor_uses_cached_value(anchor_agent):
    scene = Scene()
    scene.description = "The Starlight Nomad."
    scene.visual_anchor = "starship interior, deep space"

    with _stub_request("SHOULD NOT BE USED") as stub:
        result = await anchor_agent.scene_anchor(scene)

    assert result == "starship interior, deep space"
    assert stub.call_count == 0


# ---------------------------------------------------------------------------
# T5 — in-frame character matching
# ---------------------------------------------------------------------------


def _prompt_with_descriptive(descriptive: str, keywords: list[str] | None = None):
    """A VisualPrompt shaped like the one the LLM hands to apply_styles: a single part
    carrying both the keyword list and the descriptive prose (graph node 8cfcb710)."""
    from talemate.agents.visual.schema import VisualPrompt, VisualPromptPart

    return VisualPrompt(
        parts=[
            VisualPromptPart(
                positive_keywords_raw=keywords or ["control room"],
                positive_descriptive=descriptive,
            )
        ]
    )


def _characters(*names) -> list[Character]:
    return [Character(name=name) for name in names]


def test_in_frame_matches_full_name():
    from talemate.agents.visual.anchors import characters_in_frame

    prompt = _prompt_with_descriptive("Kaira watches the console as Elmer works.")
    matched = characters_in_frame(prompt, _characters("Kaira", "Elmer"))

    assert {c.name for c in matched} == {"Kaira", "Elmer"}


def test_in_frame_matches_first_name_only():
    from talemate.agents.visual.anchors import characters_in_frame

    prompt = _prompt_with_descriptive("Elmer leans over the console.")
    matched = characters_in_frame(prompt, _characters("Captain Elmer Farstield"))

    assert [c.name for c in matched] == ["Captain Elmer Farstield"]


def test_in_frame_excludes_absent_character():
    """AC5. An off-screen character must not contribute appearance tokens."""
    from talemate.agents.visual.anchors import characters_in_frame

    prompt = _prompt_with_descriptive("Kaira stands alone on the darkened bridge.")
    matched = characters_in_frame(prompt, _characters("Kaira", "Elmer"))

    assert [c.name for c in matched] == ["Kaira"]


def test_in_frame_handles_spaces_and_apostrophes():
    from talemate.agents.visual.anchors import characters_in_frame

    prompt = _prompt_with_descriptive("Across the bay, Se'lan O'Hara raises a hand.")
    matched = characters_in_frame(prompt, _characters("Se'lan O'Hara"))

    assert [c.name for c in matched] == ["Se'lan O'Hara"]


def test_in_frame_does_not_match_a_substring():
    """"Kai" must not match on the word "Kaira"."""
    from talemate.agents.visual.anchors import characters_in_frame

    prompt = _prompt_with_descriptive("Kaira works the console.")
    matched = characters_in_frame(prompt, _characters("Kai"))

    assert matched == []


def test_in_frame_falls_back_to_all_characters_without_descriptive():
    from talemate.agents.visual.anchors import characters_in_frame

    prompt = _prompt_with_descriptive("")
    characters = _characters("Kaira", "Elmer")
    matched = characters_in_frame(prompt, characters)

    assert [c.name for c in matched] == ["Kaira", "Elmer"]


def test_in_frame_caps_at_three_keeping_most_mentioned():
    """CLIP safety valve. Four anchors would push the action keywords off the end."""
    from talemate.agents.visual.anchors import characters_in_frame

    prompt = _prompt_with_descriptive(
        "Kaira and Kaira and Kaira. Elmer and Elmer. Dax speaks. Nel nods."
    )
    matched = characters_in_frame(prompt, _characters("Kaira", "Elmer", "Dax", "Nel"))

    assert len(matched) == 3
    assert [c.name for c in matched[:2]] == ["Kaira", "Elmer"]


# ---------------------------------------------------------------------------
# T6 — anchor insertion in apply_styles
# ---------------------------------------------------------------------------


KAIRA_ANCHOR = "alien woman, deep violet skin, indigo hair pulled back"
ELMER_ANCHOR = "human man, weathered face, close-cropped greying hair, six feet tall"
ELMER_WARDROBE = "standard-issue black EVA suit, silver rank markings"
KAIRA_WARDROBE = "fitted dark blue-grey utility suit, tool loops on belt"
SCENE_ANCHOR = "starship interior, deep space, science fiction, worn metal panelling"


@pytest.fixture
def styling_agent(kaira):
    """
    A real VisualAgent with style resolution stubbed, so these tests observe assembly
    order rather than world-state template lookup.
    """
    from talemate.agents.visual.agent import VisualAgent

    elmer = Character(
        name="Elmer",
        visual_anchor=ELMER_ANCHOR,
        visual_rules="head and face rendered completely in shadow",
    )
    kaira.visual_anchor = KAIRA_ANCHOR

    scene = Scene()
    scene.visual_anchor = SCENE_ANCHOR

    class _StyledVisualAgent(VisualAgent):
        def style_template(self, vis_type):
            return None

    agent = _StyledVisualAgent(client=_StubClient())
    agent.scene = scene
    # `characters` shadows the scene generator so the fixture does not need actors.
    agent.characters = [kaira, elmer]
    agent.kaira = kaira
    agent.elmer = elmer
    return agent


async def test_apply_styles_inserts_anchors_before_llm_keywords(styling_agent):
    """AC1. Order is load-bearing: _build_prompt dedupes first-occurrence-wins, so the
    anchors have to precede the LLM part to win position over a duplicate token."""
    from talemate.agents.visual.schema import VIS_TYPE

    prompt = _prompt_with_descriptive(
        "Kaira watches the console while Elmer leans over it.",
        keywords=["sterile control room", "flickering displays"],
    )

    await styling_agent.apply_styles(prompt, VIS_TYPE.SCENE_ILLUSTRATION)
    positive = prompt.positive_prompt

    # Only the primary subject is described - see the single-subject tests. What this
    # asserts is ordering: setting, then subject, then the LLM's action keywords.
    assert positive.index(SCENE_ANCHOR) < positive.index(KAIRA_ANCHOR)
    assert positive.index(KAIRA_ANCHOR) < positive.index("sterile control room")


async def test_apply_styles_is_byte_stable_across_calls(styling_agent):
    """AC1 proper. Two generations, identical anchor text."""
    from talemate.agents.visual.schema import VIS_TYPE

    results = []
    for _ in range(2):
        prompt = _prompt_with_descriptive(
            "Kaira watches the console while Elmer leans over it.",
            keywords=["sterile control room"],
        )
        await styling_agent.apply_styles(prompt, VIS_TYPE.SCENE_ILLUSTRATION)
        results.append(prompt.positive_prompt)

    assert results[0] == results[1]


async def test_apply_styles_carries_visual_rules(styling_agent):
    """AC4. A rule labelled HARD has to actually reach the image prompt."""
    from talemate.agents.visual.schema import VIS_TYPE

    prompt = _prompt_with_descriptive(
        "Elmer leans over the console.", keywords=["control room"]
    )

    await styling_agent.apply_styles(prompt, VIS_TYPE.SCENE_ILLUSTRATION)

    # Condensed, not verbatim: "rendered completely" is filler a diffusion model cannot
    # use. What must survive is the instruction itself.
    positive = prompt.positive_prompt
    assert "shadow" in positive
    assert "face" in positive
    assert "rendered completely" not in positive


async def test_apply_styles_omits_off_screen_characters(styling_agent):
    """AC5."""
    from talemate.agents.visual.schema import VIS_TYPE

    prompt = _prompt_with_descriptive(
        "Kaira stands alone on the darkened bridge.", keywords=["control room"]
    )

    await styling_agent.apply_styles(prompt, VIS_TYPE.SCENE_ILLUSTRATION)
    positive = prompt.positive_prompt

    assert KAIRA_ANCHOR in positive
    assert ELMER_ANCHOR not in positive
    assert "head and face rendered completely in shadow" not in positive


async def test_apply_styles_skips_character_anchors_for_object_illustration(
    styling_agent,
):
    """An object study has no cast. Anchoring the crew into it would be wrong."""
    from talemate.agents.visual.schema import VIS_TYPE

    prompt = _prompt_with_descriptive(
        "Kaira's damaged sidearm on a workbench.", keywords=["sidearm", "workbench"]
    )

    await styling_agent.apply_styles(prompt, VIS_TYPE.OBJECT_ILLUSTRATION)
    positive = prompt.positive_prompt

    assert KAIRA_ANCHOR not in positive
    assert "sidearm" in positive


# ---------------------------------------------------------------------------
# T7 — sanitising the LLM keyword part
# ---------------------------------------------------------------------------


# Verbatim from the failing generation that opened this track (request 77ae2130).
OBSERVED_BAD_PROMPT = [
    "horizontal",
    "landscape",
    "cinematic",
    "dynamic",
    "action",
    "interaction",
    "sterile control room",
    "flickering displays",
    "exhausted captain",
    "alert alien officer",
    "corruption",
    "diagnostic",
    "waiting",
    "watching",
    "tense moment",
    "geometric patterns",
    "violet skin",
    "dark circles",
    "focused",
]


def test_sanitise_drops_format_meta():
    from talemate.agents.visual.style import sanitise_keywords

    kept = sanitise_keywords(
        ["horizontal", "landscape", "cinematic framing", "sterile control room"]
    )

    assert kept == ["sterile control room"]


def test_sanitise_drops_non_visual_abstractions():
    from talemate.agents.visual.style import sanitise_keywords

    kept = sanitise_keywords(
        ["corruption", "diagnostic", "waiting", "watching", "flickering displays"]
    )

    assert kept == ["flickering displays"]


def test_sanitise_keeps_the_renderable_half_of_the_observed_prompt():
    """AC3, against the real prompt that produced a lit-faced policeman in a room with
    mountains outside the windows."""
    from talemate.agents.visual.style import sanitise_keywords

    kept = sanitise_keywords(OBSERVED_BAD_PROMPT)

    assert "sterile control room" in kept
    assert "flickering displays" in kept
    assert "violet skin" in kept
    assert "geometric patterns" in kept

    for dropped in ["horizontal", "landscape", "corruption", "waiting", "focused"]:
        assert dropped not in kept


def test_sanitise_does_not_touch_substrings_of_kept_tokens():
    """"action" is banned; "reaction shot of a chain reaction" is not what we mean, but
    neither is butchering "traction control" into "tration control"."""
    from talemate.agents.visual.style import sanitise_keywords

    kept = sanitise_keywords(["traction control", "action"])

    assert kept == ["traction control"]


def test_sanitise_is_case_insensitive():
    from talemate.agents.visual.style import sanitise_keywords

    assert sanitise_keywords(["Horizontal", "TENSION", "brass railing"]) == [
        "brass railing"
    ]


# ---------------------------------------------------------------------------
# _finalize_prompt — the real choke point
#
# apply_styles cannot sanitise or budget anything: the node graph builds the
# VisualPrompt empty, apply_styles adds styles and anchors, and the LLM's keywords are
# appended afterwards. A filter placed in apply_styles silently does nothing. Found by
# running a real generation, not by reading the graph.
# ---------------------------------------------------------------------------


def _set_budget(agent, tokens: int) -> None:
    """Drive the real config knob rather than the module default."""
    agent.actions["prompt_generation"].config["image_max_tokens"].value = tokens


def _assembled_prompt() -> str:
    """A prompt shaped exactly like the one a live generation produces: styles, scene
    anchor, both character anchors, then the LLM's keywords."""
    return ", ".join(
        [
            "score_9",
            "semi-realistic",
            SCENE_ANCHOR,
            ELMER_ANCHOR,
            "head and face rendered completely in shadow",
            KAIRA_ANCHOR,
            # the LLM's contribution, verbatim shape from the observed generation
            "Captain Elmer Farstield",
            "Kaira",
            "control room",
            "console",
            "corruption",
            "tense atmosphere",
            "frozen moment",
            "characters in action",
        ]
    )


def _request(prompt: str, vis_type=None):
    from talemate.agents.visual.schema import GenerationRequest, VIS_TYPE

    return GenerationRequest(
        prompt=prompt, vis_type=vis_type or VIS_TYPE.SCENE_ILLUSTRATION
    )


async def test_finalize_drops_the_junk_that_reached_a1111(styling_agent):
    """AC3, against the tokens observed surviving into a live A1111 payload."""
    request = _request(_assembled_prompt())

    await styling_agent._finalize_prompt(request)

    for dropped in ["corruption", "tense atmosphere", "frozen moment",
                    "characters in action"]:
        assert dropped not in request.prompt


async def test_finalize_keeps_anchors_and_styles(styling_agent):
    request = _request(_assembled_prompt())

    await styling_agent._finalize_prompt(request)

    assert SCENE_ANCHOR in request.prompt
    assert KAIRA_ANCHOR in request.prompt
    assert "score_9" in request.prompt
    assert "control room" in request.prompt


async def test_finalize_is_idempotent(styling_agent):
    """AC1 depends on this: running twice must not drift."""
    first = _request(_assembled_prompt())
    await styling_agent._finalize_prompt(first)

    second = _request(first.prompt)
    await styling_agent._finalize_prompt(second)

    assert first.prompt == second.prompt


async def test_finalize_trims_from_the_end_when_over_budget(styling_agent):
    """AC6. Styles and anchors sit at the front, so trimming the tail spends the LLM's
    action detail and protects identity."""
    _set_budget(styling_agent, 30)
    request = _request(_assembled_prompt())

    await styling_agent._finalize_prompt(request)

    assert "score_9" in request.prompt
    assert SCENE_ANCHOR.split(",")[0] in request.prompt
    assert "console" not in request.prompt


async def test_finalize_leaves_a_prompt_under_budget_alone(styling_agent):
    request = _request("score_9, " + SCENE_ANCHOR + ", control room, console")

    await styling_agent._finalize_prompt(request)

    assert "console" in request.prompt


async def test_finalize_skips_descriptive_backends(styling_agent):
    """Prose must not be split on commas - that would shred sentences."""
    from talemate.agents.visual.schema import PROMPT_TYPE

    prose = (
        "A tense moment in the control room, where the corruption spreads, "
        "horizontal and cinematic."
    )
    request = _request(prose)

    class _DescriptiveBackend:
        prompt_type = PROMPT_TYPE.DESCRIPTIVE

    styling_agent.backend = _DescriptiveBackend()
    await styling_agent._finalize_prompt(request)

    assert request.prompt == prose


async def test_finalize_drops_the_anchor_of_an_absent_character(styling_agent):
    """
    AC5. Anchors are inserted before the LLM's keywords exist, so every active character
    gets one. The LLM's keywords name who is actually in the shot; anyone unnamed loses
    their appearance keywords here.
    """
    from talemate.context import active_scene

    prompt = ", ".join(
        [
            "score_9",
            SCENE_ANCHOR,
            ELMER_ANCHOR,
            "head and face rendered completely in shadow",
            KAIRA_ANCHOR,
            # Only Kaira is named - Elmer is off-screen this shot.
            "Kaira",
            "darkened bridge",
        ]
    )
    request = _request(prompt)

    token = active_scene.set(styling_agent.scene)
    try:
        await styling_agent._finalize_prompt(request)
    finally:
        active_scene.reset(token)

    assert KAIRA_ANCHOR in request.prompt
    assert "human man" not in request.prompt
    assert "black EVA suit" not in request.prompt
    assert "head and face rendered completely in shadow" not in request.prompt
    assert "darkened bridge" in request.prompt


async def test_finalize_keeps_both_anchors_when_both_are_named(styling_agent):
    from talemate.context import active_scene

    prompt = ", ".join(
        [SCENE_ANCHOR, ELMER_ANCHOR, KAIRA_ANCHOR, "Kaira", "Elmer", "control room"]
    )
    request = _request(prompt)

    token = active_scene.set(styling_agent.scene)
    try:
        await styling_agent._finalize_prompt(request)
    finally:
        active_scene.reset(token)

    assert KAIRA_ANCHOR in request.prompt
    assert ELMER_ANCHOR in request.prompt


async def test_finalize_never_starves_the_action_keywords(styling_agent):
    """
    Regression for the failure a live generation exposed: three derived anchors filled
    the entire budget, tail-trimming removed every action keyword, and the prompt
    described two accurate people in an accurate room doing nothing at all.
    """
    from talemate.context import active_scene

    action = [
        "control room",
        "flickering displays",
        "hand gripping console edge",
        "back turned",
        "blue-white screen glow",
    ]
    prompt = ", ".join(
        [
            "score_9",
            SCENE_ANCHOR,
            ELMER_ANCHOR,
            "head and face rendered completely in shadow",
            KAIRA_ANCHOR,
            "Kaira",
            "Elmer",
        ]
        + action
    )
    request = _request(prompt)

    # Tight enough that the anchors alone would consume everything.
    _set_budget(styling_agent, 60)

    token = active_scene.set(styling_agent.scene)
    try:
        await styling_agent._finalize_prompt(request)
    finally:
        active_scene.reset(token)

    surviving_action = [kw for kw in action if kw in request.prompt]
    assert surviving_action, "every action keyword was trimmed away"

    from talemate.agents.visual.style import estimate_prompt_tokens

    assert estimate_prompt_tokens(request.prompt) <= 60


async def test_finalize_drops_extra_anchors_before_the_reserve(styling_agent):
    """The second character's appearance is sacrificed before the action reserve is."""
    from talemate.context import active_scene

    prompt = ", ".join(
        [
            SCENE_ANCHOR,
            ELMER_ANCHOR,
            KAIRA_ANCHOR,
            "Kaira",
            "Elmer",
            "hand gripping console edge",
            "blue-white screen glow",
        ]
    )
    request = _request(prompt)
    _set_budget(styling_agent, 45)

    token = active_scene.set(styling_agent.scene)
    try:
        await styling_agent._finalize_prompt(request)
    finally:
        active_scene.reset(token)

    assert "hand gripping console edge" in request.prompt
    # First character anchor survives; the second is gone.
    assert ("alien woman" in request.prompt) != ("human man" in request.prompt)


def test_sanitise_does_not_strip_the_style_templates_own_tags():
    """
    The sanitiser runs over the whole assembled prompt, styles included. Banning a
    rendering word the configured art style emits would delete that style's own tags -
    "Semi-Real (Pony)" ships exactly these.
    """
    from talemate.agents.visual.style import sanitise_keywords

    style_tags = [
        "score_9",
        "score_8_up",
        "score_7_up",
        "semi-realistic",
        "detailed painterly rendering",
        "realistic anatomy",
        "cinematic lighting",
        "sharp focus",
    ]

    assert sanitise_keywords(list(style_tags)) == style_tags


def test_sanitise_drops_category_words_observed_in_live_output():
    """Second-wave tuning: the LLM names the category instead of the detail."""
    from talemate.agents.visual.style import sanitise_keywords

    kept = sanitise_keywords(
        [
            "posture",
            "expression",
            "movement",
            "lighting",
            "painterly",
            "frustration",
            "specific moment",
            "shoulders hunched",
            "lit from below",
            "harsh shadows",
        ]
    )

    assert kept == ["shoulders hunched", "lit from below", "harsh shadows"]


def test_sanitise_drops_genre_and_plot_abstractions():
    """Third-wave tuning, from the final verification run. These name the story someone
    is in, not anything visible in the frame."""
    from talemate.agents.visual.style import sanitise_keywords

    kept = sanitise_keywords(
        [
            "mystery",
            "investigation",
            "problem-solving",
            "adventure",
            "elite crew",
            "sci-fi setting",
            "warning light",
            "recycled air",
            "metal flooring",
        ]
    )

    assert kept == ["warning light", "recycled air", "metal flooring"]


# ===========================================================================
# Track: visual-anchor-freshness
#
# Identity stays pinned; state follows the story. Previously clothing lived in the
# identity anchor, so a cached "utility suit" contradicted a scene saying "naked".
# ===========================================================================


WARDROBE_TOKENS_IN_OLD_ANCHOR = (
    "alien woman, deep violet skin, indigo hair pulled back, "
    "fitted dark blue-grey utility suit, tool loops and pockets on belt"
)


# --- T3: visual_wardrobe field -------------------------------------------------


def test_character_visual_wardrobe_defaults_to_none(kaira):
    assert kaira.visual_wardrobe is None


def test_character_visual_wardrobe_round_trips(kaira):
    kaira.visual_wardrobe = "bare feet, smudged with soot"

    restored = Character(**kaira.model_dump())

    assert restored.visual_wardrobe == kaira.visual_wardrobe


# --- T2: one-time migration ---------------------------------------------------


def test_strip_wardrobe_tokens_removes_garments():
    from talemate.agents.visual.anchors import strip_wardrobe_tokens

    result = strip_wardrobe_tokens(WARDROBE_TOKENS_IN_OLD_ANCHOR)

    assert result == "alien woman, deep violet skin, indigo hair pulled back"


def test_strip_wardrobe_tokens_leaves_identity_only_anchors_alone():
    from talemate.agents.visual.anchors import strip_wardrobe_tokens

    clean = "alien woman, deep violet skin, four-fingered hands, lean and tall"

    assert strip_wardrobe_tokens(clean) == clean


def test_strip_wardrobe_tokens_is_idempotent():
    from talemate.agents.visual.anchors import strip_wardrobe_tokens

    once = strip_wardrobe_tokens(WARDROBE_TOKENS_IN_OLD_ANCHOR)

    assert strip_wardrobe_tokens(once) == once


def test_strip_wardrobe_tokens_keeps_body_words_that_merely_look_like_clothing():
    """"barefoot" is a state, but "bare shoulders" describes the body. Over-stripping
    would quietly delete identity detail."""
    from talemate.agents.visual.anchors import strip_wardrobe_tokens

    result = strip_wardrobe_tokens("lean and tall, broad shoulders, four-fingered hands")

    assert result == "lean and tall, broad shoulders, four-fingered hands"


def test_character_migrates_a_cached_anchor_on_construction():
    """AC7. Runs for every construction path - scene load, character card import, copy -
    because it is a model validator rather than a hook at one call site."""
    character = Character(
        name="Kaira", visual_anchor=WARDROBE_TOKENS_IN_OLD_ANCHOR
    )

    assert "utility suit" not in character.visual_anchor
    assert "deep violet skin" in character.visual_anchor


def test_character_migration_does_not_touch_wardrobe_field():
    character = Character(
        name="Kaira",
        visual_anchor=WARDROBE_TOKENS_IN_OLD_ANCHOR,
        visual_wardrobe="fitted dark blue-grey utility suit",
    )

    assert character.visual_wardrobe == "fitted dark blue-grey utility suit"


# --- T7: appearance edit invalidates the anchor --------------------------------


@pytest.fixture
def stub_memory_agent():
    """set_base_attribute commits to memory; these tests care only about the anchor."""
    with patch("talemate.instance.get_agent", return_value=AsyncMock()):
        yield


async def test_setting_appearance_clears_the_identity_anchor(kaira, stub_memory_agent):
    """AC4. Previously the anchor went stale silently and permanently."""
    kaira.visual_anchor = "alien woman, deep violet skin"

    await kaira.set_base_attribute("appearance", "Now has a livid scar across her jaw.")

    assert kaira.visual_anchor is None


async def test_setting_another_attribute_leaves_the_anchor_alone(
    kaira, stub_memory_agent
):
    kaira.visual_anchor = "alien woman, deep violet skin"

    await kaira.set_base_attribute("personality", "Warmer than she used to be.")

    assert kaira.visual_anchor == "alien woman, deep violet skin"


# --- T4/T5: wardrobe derivation and its reinforcement -------------------------


WARDROBE_QUESTION = "What is {name} currently wearing, and what visible physical condition are they in?"


async def test_wardrobe_anchor_derives_from_a_report(kaira, anchor_agent):
    derived = "bare feet, sleeveless undershirt, smudged with soot"

    with _stub_request(derived) as stub:
        result = await anchor_agent.wardrobe_anchor(
            kaira, "She has stripped off the EVA suit and stands barefoot."
        )

    assert result == derived
    assert kaira.visual_wardrobe == derived
    assert stub.call_count == 1


async def test_wardrobe_anchor_reuses_cache_for_an_unchanged_report(kaira, anchor_agent):
    """AC3's cost control. The reinforcement re-runs on a cadence whether or not the
    answer moved; only a changed answer should cost a derivation."""
    report = "Wearing the utility suit."

    with _stub_request("fitted utility suit") as stub:
        await anchor_agent.wardrobe_anchor(kaira, report)
        await anchor_agent.wardrobe_anchor(kaira, report)

    assert stub.call_count == 1


async def test_wardrobe_anchor_re_derives_when_the_report_changes(kaira, anchor_agent):
    with _stub_request("fitted utility suit") as stub:
        await anchor_agent.wardrobe_anchor(kaira, "Wearing the utility suit.")
    assert stub.call_count == 1

    with _stub_request("bare feet, undershirt") as stub:
        result = await anchor_agent.wardrobe_anchor(kaira, "She has undressed.")

    assert stub.call_count == 1
    assert result == "bare feet, undershirt"
    assert kaira.visual_wardrobe == "bare feet, undershirt"


async def test_wardrobe_anchor_ignores_an_empty_report(kaira, anchor_agent):
    with _stub_request("something") as stub:
        result = await anchor_agent.wardrobe_anchor(kaira, "")

    assert result is None
    assert stub.call_count == 0


async def test_ensure_wardrobe_reinforcement_is_created_once(kaira, anchor_agent):
    """Reinforcements persist with the scene, so a reload must not stack duplicates."""
    scene = Scene()
    scene.character_data = {"Kaira": kaira}

    await anchor_agent.ensure_wardrobe_reinforcement(scene, kaira)
    await anchor_agent.ensure_wardrobe_reinforcement(scene, kaira)

    matching = [
        r for r in scene.world_state.reinforce
        if r.character == "Kaira" and "wearing" in r.question.lower()
    ]
    assert len(matching) == 1


async def test_wardrobe_reinforcement_never_enters_story_context(kaira, anchor_agent):
    """insert="never" - the image prompt consumes the answer, the story does not, and
    the history-flooding path in update_reinforcement is sequential-only."""
    scene = Scene()
    scene.character_data = {"Kaira": kaira}

    await anchor_agent.ensure_wardrobe_reinforcement(scene, kaira)

    reinforcement = next(
        r for r in scene.world_state.reinforce if r.character == "Kaira"
    )
    assert reinforcement.insert == "never"


# --- T6: wardrobe is a fallback the scene can suppress ------------------------


def _wardrobe_prompt(*, scene_says: list[str]) -> str:
    """Assembled prompt shape: styles, setting, identity, wardrobe, then the LLM."""
    return ", ".join(
        [
            "score_9",
            SCENE_ANCHOR,
            KAIRA_ANCHOR,
            KAIRA_WARDROBE,
            "Kaira",
        ]
        + scene_says
    )


async def _finalize(agent, prompt: str):
    from talemate.context import active_scene

    request = _request(prompt)
    token = active_scene.set(agent.scene)
    try:
        await agent._finalize_prompt(request)
    finally:
        active_scene.reset(token)
    return request.prompt


async def test_wardrobe_conflict_scene_says_undressed(styling_agent):
    """
    AC1. The failure that started this track: a cached "utility suit" and a scene saying
    "naked" both reached the model, which rendered a compromise garment.
    """
    styling_agent.kaira.visual_wardrobe = KAIRA_WARDROBE

    result = await _finalize(
        styling_agent, _wardrobe_prompt(scene_says=["naked", "bare feet"])
    )

    assert "utility suit" not in result
    assert "tool loops on belt" not in result
    assert "naked" in result


async def test_wardrobe_conflict_scene_names_other_clothing(styling_agent):
    styling_agent.kaira.visual_wardrobe = KAIRA_WARDROBE

    result = await _finalize(
        styling_agent, _wardrobe_prompt(scene_says=["torn flight jacket", "soaked"])
    )

    assert "utility suit" not in result
    assert "torn flight jacket" in result


async def test_wardrobe_survives_when_the_scene_is_silent_on_clothing(styling_agent):
    """Silence is the case wardrobe exists for - the cached outfit is the best guess."""
    styling_agent.kaira.visual_wardrobe = KAIRA_WARDROBE

    result = await _finalize(
        styling_agent, _wardrobe_prompt(scene_says=["control room", "harsh shadows"])
    )

    assert "fitted dark blue-grey utility suit" in result


async def test_wardrobe_suppression_never_touches_identity(styling_agent):
    """AC2. Identity is permanent; a clothing change must not disturb it."""
    styling_agent.kaira.visual_wardrobe = KAIRA_WARDROBE

    dressed = await _finalize(
        styling_agent, _wardrobe_prompt(scene_says=["control room"])
    )
    undressed = await _finalize(styling_agent, _wardrobe_prompt(scene_says=["naked"]))

    for token in KAIRA_ANCHOR.split(", "):
        assert token in dressed
        assert token in undressed
    assert SCENE_ANCHOR in dressed and SCENE_ANCHOR in undressed


async def test_insert_anchors_includes_wardrobe_after_identity(styling_agent):
    """Wardrobe must sit after identity so suppression can still remove it downstream."""
    from talemate.agents.visual.schema import VIS_TYPE

    styling_agent.kaira.visual_wardrobe = KAIRA_WARDROBE
    styling_agent.kaira._wardrobe_fingerprint = None
    styling_agent.scene.world_state.reinforce = []

    prompt = _prompt_with_descriptive(
        "Kaira watches the console.", keywords=["control room"]
    )
    await styling_agent.apply_styles(prompt, VIS_TYPE.SCENE_ILLUSTRATION)
    positive = prompt.positive_prompt

    assert KAIRA_WARDROBE.split(",")[0].strip() in positive
    assert positive.index(KAIRA_ANCHOR.split(",")[0]) < positive.index(
        KAIRA_WARDROBE.split(",")[0].strip()
    )


async def test_freshness_switch_off_stops_new_derivations(styling_agent):
    """Off keeps what was already learned but adds no reinforcement and no LLM call."""
    styling_agent.actions["_freshness"].config["enabled"].value = False
    styling_agent.kaira.visual_wardrobe = KAIRA_WARDROBE
    styling_agent.scene.world_state.reinforce = []

    with _stub_request("SHOULD NOT BE USED") as stub:
        result = await styling_agent.refresh_wardrobe(
            styling_agent.scene, styling_agent.kaira
        )

    assert result == KAIRA_WARDROBE
    assert stub.call_count == 0
    assert styling_agent.scene.world_state.reinforce == []


async def test_refresh_wardrobe_creates_the_reinforcement_and_uses_its_answer(
    styling_agent,
):
    from talemate.agents.visual.anchors import WARDROBE_QUESTION

    styling_agent.kaira.visual_wardrobe = None
    styling_agent.kaira._wardrobe_fingerprint = None
    styling_agent.scene.world_state.reinforce = []

    await styling_agent.refresh_wardrobe(styling_agent.scene, styling_agent.kaira)

    _, reinforcement = await styling_agent.scene.world_state.find_reinforcement(
        WARDROBE_QUESTION, "Kaira"
    )
    assert reinforcement is not None
    reinforcement.answer = "She has stripped to an undershirt and bare feet."

    with _stub_request("undershirt, bare feet") as stub:
        result = await styling_agent.refresh_wardrobe(
            styling_agent.scene, styling_agent.kaira
        )

    assert result == "undershirt, bare feet"
    assert stub.call_count == 1


# --- T8: permanent-change detection, with false-positive containment -----------


@pytest.mark.parametrize(
    "answer",
    [
        "No.",
        "no",
        "",
        "   ",
        "Nothing has changed.",
        "No permanent changes.",
        "None that I can see.",
        "Not that the story states.",
        "Unchanged.",
        "Possibly, but it is unclear.",
        "It's hard to say for certain.",
        "She might have a new scar, though this is not stated.",
    ],
)
def test_permanent_change_ignores_negative_and_hedged_answers(answer):
    """
    AC8, and the whole risk of this task. An LLM asked "did anything change?" will oblige
    if it can. Anything short of a specific assertion must not clear an anchor.
    """
    from talemate.agents.visual.anchors import asserts_permanent_change

    assert asserts_permanent_change(answer) is False


@pytest.mark.parametrize(
    "answer",
    [
        "Yes - she now has a livid scar across her jaw.",
        "Her left hand was severed at the wrist.",
        "She has cut her hair short and dyed it black.",
        "He lost his right eye in the blast and wears the socket uncovered.",
    ],
)
def test_permanent_change_accepts_specific_assertions(answer):
    from talemate.agents.visual.anchors import asserts_permanent_change

    assert asserts_permanent_change(answer) is True


async def test_permanent_change_clears_the_identity_anchor(kaira, anchor_agent):
    scene = Scene()
    scene.character_data = {"Kaira": kaira}
    kaira.visual_anchor = "alien woman, deep violet skin"

    await anchor_agent.check_permanent_change(
        scene, kaira, "Yes - she now has a livid scar across her jaw."
    )

    assert kaira.visual_anchor is None


async def test_permanent_change_leaves_the_anchor_on_a_hedge(kaira, anchor_agent):
    scene = Scene()
    scene.character_data = {"Kaira": kaira}
    kaira.visual_anchor = "alien woman, deep violet skin"

    await anchor_agent.check_permanent_change(
        scene, kaira, "Possibly, but it is unclear."
    )

    assert kaira.visual_anchor == "alien woman, deep violet skin"


async def test_permanent_change_is_rate_limited(kaira, anchor_agent):
    """Two assertions inside the window must cause one invalidation, not two. Otherwise a
    chatty detector re-derives the anchor every few turns and identity drifts again."""
    scene = Scene()
    scene.character_data = {"Kaira": kaira}
    answer = "Yes - a fresh burn scar across her forearm."

    kaira.visual_anchor = "alien woman, deep violet skin"
    first = await anchor_agent.check_permanent_change(scene, kaira, answer)

    kaira.visual_anchor = "alien woman, deep violet skin, burn scar"
    second = await anchor_agent.check_permanent_change(
        scene, kaira, "Yes - and now a split lip too."
    )

    assert first is True
    assert second is False
    assert kaira.visual_anchor == "alien woman, deep violet skin, burn scar"


# --- T9/T10: location-keyed scene anchors --------------------------------------


def test_normalize_location_key_collapses_variants():
    from talemate.agents.visual.anchors import normalize_location_key

    assert normalize_location_key("The Control Room") == normalize_location_key(
        "control room"
    )
    assert normalize_location_key("  Bridge   Deck ") == normalize_location_key(
        "bridge deck"
    )
    assert normalize_location_key(None) is None
    assert normalize_location_key("   ") is None


def test_scene_visual_anchors_dict_round_trips():
    scene = Scene()
    scene.visual_anchors = {"control room": "starship interior, deep space"}

    assert scene.serialize["visual_anchors"] == scene.visual_anchors


async def test_scene_anchor_caches_per_location(anchor_agent):
    """AC5/AC6. Each location gets its own anchor, and revisiting costs nothing."""
    scene = Scene()
    scene.description = "The Starlight Nomad."
    scene.world_state.location = "The Control Room"

    with _stub_request("starship interior, consoles") as stub:
        first = await anchor_agent.scene_anchor(scene)
    assert first == "starship interior, consoles"
    assert stub.call_count == 1

    # Same location, differently cased - must hit the cache.
    scene.world_state.location = "control room"
    with _stub_request("SHOULD NOT BE USED") as stub:
        again = await anchor_agent.scene_anchor(scene)
    assert again == "starship interior, consoles"
    assert stub.call_count == 0

    # New location - derives and caches separately.
    scene.world_state.location = "The derelict structure's interior"
    with _stub_request("alien architecture, glowing runes") as stub:
        second = await anchor_agent.scene_anchor(scene)
    assert second == "alien architecture, glowing runes"
    assert stub.call_count == 1

    # Returning to the first location reuses its anchor, not the newest one.
    scene.world_state.location = "control room"
    with _stub_request("SHOULD NOT BE USED") as stub:
        back = await anchor_agent.scene_anchor(scene)
    assert back == "starship interior, consoles"
    assert stub.call_count == 0


async def test_scene_anchor_falls_back_when_location_unknown(anchor_agent):
    """No location known - behave exactly as before this change."""
    scene = Scene()
    scene.description = "The Starlight Nomad."
    scene.world_state.location = None

    with _stub_request("starship interior, deep space") as stub:
        result = await anchor_agent.scene_anchor(scene)

    assert result == "starship interior, deep space"
    assert scene.visual_anchor == "starship interior, deep space"
    assert stub.call_count == 1


async def test_scene_anchor_prefers_location_entry_over_the_legacy_field(anchor_agent):
    """A scene saved before this change has visual_anchor set; once a location is known,
    that location's own anchor governs."""
    scene = Scene()
    scene.description = "The Starlight Nomad."
    scene.visual_anchor = "starship interior, deep space"
    scene.world_state.location = "planet surface"

    with _stub_request("red desert, dust storm, alien sky") as stub:
        result = await anchor_agent.scene_anchor(scene)

    assert result == "red desert, dust storm, alien sky"
    assert stub.call_count == 1


# ===========================================================================
# Prompt legibility
#
# The anchors were stable but the prompt was not something SDXL could parse: two full
# character descriptions in one flat prompt have no way to bind attributes to subjects,
# so "human male" and "alien woman" blend. Observed live at 45 keywords / ~155 tokens,
# with the setting stated twice and authored prose reaching the model verbatim.
# ===========================================================================


def test_condense_visual_rule_strips_meta_prose():
    """"The user controlled character" is meaningless to a diffusion model and cost 13
    tokens of a 77-token budget."""
    from talemate.agents.visual.anchors import condense_visual_rule

    result = condense_visual_rule(
        "The user controlled character - always has the head / face rendered completely in shadows"
    )

    assert "user controlled character" not in result
    assert "always" not in result
    assert "shadow" in result
    assert len(result.split()) <= 6


def test_condense_visual_rule_preserves_negations_verbatim():
    """Stripping filler from a negated rule could invert it. Left alone instead - a
    slightly long rule is survivable, an inverted one is not."""
    from talemate.agents.visual.anchors import condense_visual_rule

    rule = "Never show her left hand, it is always gloved"
    result = condense_visual_rule(rule)

    assert "never" in result.lower()
    assert "left hand" in result


def test_condense_visual_rule_leaves_keyword_style_rules_alone():
    from talemate.agents.visual.anchors import condense_visual_rule

    assert condense_visual_rule("cybernetic left arm") == "cybernetic left arm"


async def test_only_the_primary_character_gets_a_full_anchor(styling_agent):
    """
    A flat prompt cannot bind attributes to two subjects. One full identity, and the
    others counted rather than described, so the model is told there are two people
    without being told two contradictory things about one.
    """
    from talemate.agents.visual.schema import VIS_TYPE

    prompt = _prompt_with_descriptive(
        "Kaira watches the console while Elmer leans over it.",
        keywords=["control room"],
    )
    await styling_agent.apply_styles(prompt, VIS_TYPE.SCENE_ILLUSTRATION)
    positive = prompt.positive_prompt

    anchored = [a for a in (KAIRA_ANCHOR, ELMER_ANCHOR) if a.split(",")[0] in positive]
    assert len(anchored) == 1, f"expected one anchored subject, got {anchored}"
    assert "second figure" in positive


async def test_single_character_scene_gets_no_extra_figure_token(styling_agent):
    from talemate.agents.visual.schema import VIS_TYPE

    prompt = _prompt_with_descriptive(
        "Kaira stands alone on the darkened bridge.", keywords=["control room"]
    )
    await styling_agent.apply_styles(prompt, VIS_TYPE.SCENE_ILLUSTRATION)

    assert "second figure" not in prompt.positive_prompt


async def test_llm_setting_keywords_are_dropped_when_an_anchor_supplied_them(
    styling_agent,
):
    """The setting was being described twice - once by the anchor, once by the LLM, in
    words that disagreed ("control room" against "Starship bridge")."""
    from talemate.context import active_scene

    prompt = ", ".join(
        [SCENE_ANCHOR, KAIRA_ANCHOR, "Kaira", "starship bridge", "viewport",
         "hand gripping console edge"]
    )
    request = _request(prompt)

    token = active_scene.set(styling_agent.scene)
    try:
        await styling_agent._finalize_prompt(request)
    finally:
        active_scene.reset(token)

    assert "starship bridge" not in request.prompt.lower()
    assert "hand gripping console edge" in request.prompt
    assert SCENE_ANCHOR.split(",")[0] in request.prompt


def test_configured_budget_matches_the_module_default():
    """
    The config value is what applies; the module constant is only a fallback. They drifted
    once - the default was lowered to 77 while config still said 250, so the change did
    nothing and a 104-token prompt shipped.
    """
    from talemate.agents.visual.agent import VisualAgent
    from talemate.agents.visual.style import DEFAULT_MAX_PROMPT_TOKENS

    actions = VisualAgent.init_actions()
    configured = actions["prompt_generation"].config["image_max_tokens"].value

    assert configured == DEFAULT_MAX_PROMPT_TOKENS


def test_normalize_keyword_separators_splits_underscore_tokens():
    """
    The LLM sometimes emits "starship_bridge, naked_violet_skin, moment_of_tension".
    Underscores are word characters, so every comparison in the pipeline - blocklist,
    name matching, duplicate-setting overlap - silently fails on them.
    """
    from talemate.agents.visual.style import normalize_keyword

    assert normalize_keyword("starship_bridge") == "starship bridge"
    assert normalize_keyword("moment_of_tension") == "moment of tension"
    assert normalize_keyword("score_9") == "score_9"
    assert normalize_keyword("score_8_up") == "score_8_up"
    assert normalize_keyword("deep violet skin") == "deep violet skin"


async def test_absent_character_pruning_is_skipped_when_no_name_appears(styling_agent):
    """
    Regression for a bad failure mode: the LLM produced keywords naming nobody, every
    character read as off-screen, and both identity anchors were stripped - leaving a
    prompt with no subject at all. Absent evidence is not evidence of absence.
    """
    from talemate.context import active_scene

    prompt = ", ".join(
        [SCENE_ANCHOR, KAIRA_ANCHOR, "console", "red glow", "leaning forward"]
    )
    request = _request(prompt)

    token = active_scene.set(styling_agent.scene)
    try:
        await styling_agent._finalize_prompt(request)
    finally:
        active_scene.reset(token)

    assert "alien woman" in request.prompt


async def test_prompt_is_trimmed_to_the_configured_budget(styling_agent):
    from talemate.context import active_scene
    from talemate.agents.visual.style import estimate_prompt_tokens

    filler = [f"filler detail {n}" for n in range(40)]
    request = _request(", ".join([SCENE_ANCHOR, KAIRA_ANCHOR, "Kaira"] + filler))

    token = active_scene.set(styling_agent.scene)
    try:
        await styling_agent._finalize_prompt(request)
    finally:
        active_scene.reset(token)

    budget = styling_agent.actions["prompt_generation"].config["image_max_tokens"].value
    assert estimate_prompt_tokens(request.prompt) <= budget


async def test_setting_filter_never_eats_action_detail(styling_agent):
    """
    A long setting anchor is full of common nouns - console, metal, space - and matching
    on shared vocabulary alone removed real action detail, leaving prompts that described
    a room and nobody in it.
    """
    from talemate.context import active_scene

    long_anchor = (
        "control room, spaceship interior, deep space, science fiction, "
        "digital displays, metal framework, navigation console"
    )
    styling_agent.scene.visual_anchor = long_anchor
    styling_agent.scene.visual_anchors = {}
    styling_agent.scene.world_state.location = None

    action = [
        "leaning over console",
        "hand gripping console edge",
        "feet planted on metal deck",
        "watching the displays",
    ]
    request = _request(", ".join([long_anchor, KAIRA_ANCHOR, "Kaira"] + action))

    token = active_scene.set(styling_agent.scene)
    try:
        await styling_agent._finalize_prompt(request)
    finally:
        active_scene.reset(token)

    for kw in action:
        assert kw in request.prompt, f"action detail was eaten: {kw}"


# ===========================================================================
# Prompt adherence
#
# Stable, clean prompts turned out to be necessary but not sufficient. Observed: the
# setting binds correctly while per-subject attributes do not - "deep violet skin" came
# back near-human, "naked" came back in a tank top, "face in shadow" came back lit. That
# is SDXL attribute binding, made worse at cfg 5. These are the prompt-level levers.
# ===========================================================================


def test_weight_identity_wraps_the_anchor_group():
    """A1111 reads (text:1.3) as emphasis. Applied to the identity group as a whole
    rather than per token, which would cost a bracket pair each."""
    from talemate.agents.visual.style import weight_group

    assert weight_group(["alien woman", "violet skin"], 1.3) == (
        "(alien woman, violet skin:1.3)"
    )


def test_weight_identity_is_a_no_op_at_weight_one():
    from talemate.agents.visual.style import weight_group

    assert weight_group(["alien woman"], 1.0) == "alien woman"


def test_weight_identity_escapes_existing_parentheses():
    """An unescaped bracket in an anchor would change how the whole prompt parses."""
    from talemate.agents.visual.style import weight_group

    assert weight_group(["scar (old)"], 1.2) == r"(scar \(old\):1.2)"


async def test_finalize_weights_the_identity_anchor(styling_agent):
    from talemate.context import active_scene

    styling_agent.actions["prompt_generation"].config["identity_weight"].value = 1.3
    request = _request(", ".join([SCENE_ANCHOR, KAIRA_ANCHOR, "Kaira", "leaning over"]))

    token = active_scene.set(styling_agent.scene)
    try:
        await styling_agent._finalize_prompt(request)
    finally:
        active_scene.reset(token)

    assert f"({KAIRA_ANCHOR}:1.3)" in request.prompt
    # Setting and action are left unweighted.
    assert SCENE_ANCHOR in request.prompt
    assert "leaning over" in request.prompt


async def test_finalize_adds_species_negatives_for_a_non_human_subject(styling_agent):
    """The checkpoint's prior is overwhelmingly human, which is why violet skin comes
    back muted. Push back in the negative prompt as well as the positive."""
    from talemate.context import active_scene

    styling_agent.kaira.base_attributes["species"] = "Altrusian"
    request = _request(", ".join([KAIRA_ANCHOR, "Kaira"]))
    request.negative_prompt = "text, watermark"

    token = active_scene.set(styling_agent.scene)
    try:
        await styling_agent._finalize_prompt(request)
    finally:
        active_scene.reset(token)

    assert "human skin" in request.negative_prompt
    assert "text, watermark" in request.negative_prompt


async def test_finalize_adds_no_species_negatives_for_a_human_subject(styling_agent):
    from talemate.context import active_scene

    styling_agent.kaira.base_attributes["species"] = "Human"
    request = _request(", ".join([KAIRA_ANCHOR, "Kaira"]))
    request.negative_prompt = "text, watermark"

    token = active_scene.set(styling_agent.scene)
    try:
        await styling_agent._finalize_prompt(request)
    finally:
        active_scene.reset(token)

    assert "human skin" not in request.negative_prompt
