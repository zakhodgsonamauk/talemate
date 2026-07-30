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
    kaira.visual_anchor = "violet skin, indigo hair, dark blue-grey utility suit"

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


@pytest.fixture
def anchor_agent():
    """Minimal object carrying AnchorMixin plus the client attribute it needs."""
    from talemate.agents.visual.anchors import AnchorMixin

    class _Agent(AnchorMixin):
        client = object()

    return _Agent()


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
ELMER_ANCHOR = "human man, weathered face, close-cropped greying hair, black EVA suit"
SCENE_ANCHOR = "starship interior, deep space, science fiction, worn metal panelling"


@pytest.fixture
def styling_agent(kaira):
    """Agent carrying both mixins, with style templates and derivation stubbed out so
    the test observes ordering rather than template resolution."""
    from talemate.agents.visual.anchors import AnchorMixin
    from talemate.agents.visual.style import StyleMixin

    elmer = Character(
        name="Elmer",
        visual_anchor=ELMER_ANCHOR,
        visual_rules="head and face rendered completely in shadow",
    )
    kaira.visual_anchor = KAIRA_ANCHOR

    scene = Scene()
    scene.visual_anchor = SCENE_ANCHOR

    class _Agent(AnchorMixin, StyleMixin):
        client = object()

        def __init__(self):
            self.scene = scene
            self.characters = [kaira, elmer]

        def style_template(self, vis_type):
            return None

    agent = _Agent()
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

    assert positive.index(SCENE_ANCHOR) < positive.index(KAIRA_ANCHOR)
    assert positive.index(KAIRA_ANCHOR) < positive.index("sterile control room")
    assert positive.index(ELMER_ANCHOR) < positive.index("sterile control room")


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

    assert "head and face rendered completely in shadow" in prompt.positive_prompt


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


async def test_apply_styles_sanitises_only_the_llm_part(styling_agent):
    """The anchors and styles are ours and are already clean. Only the LLM's list gets
    filtered - and its descriptive prose must survive, because the in-frame matcher
    reads it and a DESCRIPTIVE backend would ship it."""
    from talemate.agents.visual.schema import VIS_TYPE

    prompt = _prompt_with_descriptive(
        "Kaira watches the console. The composition is horizontal and cinematic.",
        keywords=OBSERVED_BAD_PROMPT,
    )

    await styling_agent.apply_styles(prompt, VIS_TYPE.SCENE_ILLUSTRATION)
    positive = prompt.positive_prompt

    assert "horizontal" not in positive
    assert "corruption" not in positive
    assert KAIRA_ANCHOR in positive
    assert SCENE_ANCHOR in positive

    surviving_descriptive = " ".join(
        part.positive_descriptive for part in prompt.parts if part.positive_descriptive
    )
    assert "horizontal and cinematic" in surviving_descriptive


# ---------------------------------------------------------------------------
# T8 — prompt budget
# ---------------------------------------------------------------------------


def test_estimate_prompt_tokens_counts_words_and_separators():
    from talemate.agents.visual.style import estimate_prompt_tokens

    assert estimate_prompt_tokens("") == 0
    # 3 words + 1 comma separator
    assert estimate_prompt_tokens("violet skin, tall") == 4


async def test_budget_drops_llm_keywords_first(styling_agent, monkeypatch):
    """AC6. When something has to go, the LLM's action keywords go before the anchors -
    a wrong-looking character is worse than a vaguer action."""
    from talemate.agents.visual import style as style_module
    from talemate.agents.visual.schema import VIS_TYPE

    monkeypatch.setattr(style_module, "DEFAULT_MAX_PROMPT_TOKENS", 20)

    prompt = _prompt_with_descriptive(
        "Kaira watches the console while Elmer leans over it.",
        keywords=[f"filler keyword {n}" for n in range(40)],
    )

    await styling_agent.apply_styles(prompt, VIS_TYPE.SCENE_ILLUSTRATION)
    positive = prompt.positive_prompt

    assert KAIRA_ANCHOR.split(",")[0] in positive
    assert "filler keyword 39" not in positive


async def test_budget_never_drops_the_first_character_anchor(
    styling_agent, monkeypatch
):
    """Even at an absurd budget the subject keeps its identity tokens. An image of the
    wrong person is a worse failure than an over-long prompt."""
    from talemate.agents.visual import style as style_module
    from talemate.agents.visual.schema import VIS_TYPE

    monkeypatch.setattr(style_module, "DEFAULT_MAX_PROMPT_TOKENS", 1)

    prompt = _prompt_with_descriptive(
        "Kaira watches the console while Elmer leans over it.",
        keywords=["sterile control room"],
    )

    await styling_agent.apply_styles(prompt, VIS_TYPE.SCENE_ILLUSTRATION)
    positive = prompt.positive_prompt

    assert KAIRA_ANCHOR in positive
    assert ELMER_ANCHOR not in positive, "second anchor should have been dropped"
    assert SCENE_ANCHOR not in positive, "scene anchor should have been dropped"


async def test_budget_leaves_a_prompt_under_budget_alone(styling_agent):
    from talemate.agents.visual.schema import VIS_TYPE

    prompt = _prompt_with_descriptive(
        "Kaira watches the console while Elmer leans over it.",
        keywords=["sterile control room", "flickering displays"],
    )

    await styling_agent.apply_styles(prompt, VIS_TYPE.SCENE_ILLUSTRATION)
    positive = prompt.positive_prompt

    assert "sterile control room" in positive
    assert "flickering displays" in positive
    assert KAIRA_ANCHOR in positive
    assert ELMER_ANCHOR in positive
    assert SCENE_ANCHOR in positive
