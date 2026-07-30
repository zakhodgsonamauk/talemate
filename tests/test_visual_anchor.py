"""Tests for visual anchors — the deterministic character/scene appearance tokens
that keep generated illustrations consistent.

See docs/fork/visual-consistency-design.md. The short version: appearance used to be
re-derived by the LLM on every generation, so the subject could not be stable. An
anchor is derived once from canonical prose, cached on the character, and injected
verbatim from then on.
"""

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
