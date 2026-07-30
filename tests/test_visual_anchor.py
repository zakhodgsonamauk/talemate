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
