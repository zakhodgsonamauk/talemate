"""
Unit tests for visual agent templates.

NOTE: For per-generation image prompts the Visual agent does NOT have Python methods
that directly call Prompt.request(). It uses a workflow/graph-based system where
templates are loaded and rendered via nodes like PromptFromTemplate and LoadTemplate in
JSON workflow files (agents/visual/modules/generate-visual-asset.json).

The one exception is visual/derive-visual-anchor.jinja2, which IS called directly from
Python (agents/visual/anchors.py). It runs once per subject and caches its result, so
it sits outside the per-generation graph.

Templates used by the Visual agent:
- generate-image.jinja2 - Main template loaded via PromptFromTemplate node in workflow
- refine-prompt.jinja2 - Loaded via LoadTemplate node in workflow
- extra-context.jinja2 - Included by generate-image.jinja2
- generate-image-prompt-type.jinja2 - Included by generate-image.jinja2
- generate-image-UNSPECIFIED.jinja2 - Dynamically included by generate-image.jinja2
- generate-image-CHARACTER_PORTRAIT.jinja2 - Dynamically included by generate-image.jinja2
- generate-image-CHARACTER_CARD.jinja2 - Dynamically included by generate-image.jinja2
- generate-image-SCENE_BACKGROUND.jinja2 - Dynamically included by generate-image.jinja2
- generate-image-SCENE_ILLUSTRATION.jinja2 - Dynamically included by generate-image.jinja2
- generate-image-SCENE_CARD.jinja2 - Dynamically included by generate-image.jinja2
- derive-visual-anchor.jinja2 - Called directly from agents/visual/anchors.py
- system.jinja2 - Used via system_prompts module for LLM system prompts
- system-no-decensor.jinja2 - Used via system_prompts module for LLM system prompts

Deprecated/unused templates (see plan/managed-prompt-templates/DEPRECATED_TEMPLATES.md):
- generate-scene-prompt.jinja2 - Not used in any code path
- generate-environment-prompt.jinja2 - Not used in any code path

The system prompts (visual.system, visual.system-no-decensor) are tested implicitly
through the system_prompts module which handles their rendering.
"""

import pytest

from talemate.character import Character

from .helpers import render_template


KAIRA_APPEARANCE = (
    "Just over six feet, lean and angular. Deep violet skin with faint geometric "
    "patterns along her forearms and jaw. Large dark eyes, no visible iris. Hair is a "
    "deep indigo, worn pulled back and secured. Four-fingered hands. Wears a fitted "
    "utility suit in dark blue-grey."
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
# derive-visual-anchor - character mode
# ---------------------------------------------------------------------------


def test_derive_anchor_character_mode_includes_appearance(kaira):
    rendered = render_template(
        "visual.derive-visual-anchor",
        vars={"anchor_mode": "character", "character": kaira},
    )

    assert KAIRA_APPEARANCE in rendered
    assert "Altrusian" in rendered
    assert "<ANCHOR></ANCHOR>" in rendered


def test_derive_anchor_character_mode_states_the_constraints(kaira):
    """The constraints are the whole point. Without them the LLM returns personality and
    plot words, which is what polluted the image prompts in the first place."""
    rendered = render_template(
        "visual.derive-visual-anchor",
        vars={"anchor_mode": "character", "character": kaira},
    )

    assert "Only physically visible things" in rendered
    assert "No personality, no history, no plot" in rendered
    assert "No camera or format language" in rendered
    assert "No art style" in rendered
    assert "Invent nothing" in rendered


def test_derive_anchor_character_mode_falls_back_to_description():
    """A character with no appearance attribute still has to produce something."""
    bare = Character(name="Nomad", description="A tall figure in a grey coat.")

    rendered = render_template(
        "visual.derive-visual-anchor",
        vars={"anchor_mode": "character", "character": bare},
    )

    assert "A tall figure in a grey coat." in rendered
    assert "No appearance attribute exists" in rendered


# ---------------------------------------------------------------------------
# derive-visual-anchor - scene mode
# ---------------------------------------------------------------------------


def test_derive_anchor_scene_mode_includes_premise(mock_scene):
    mock_scene.description = "The Starlight Nomad, a deep-space survey vessel."
    mock_scene.context = "science fiction"
    mock_scene.title = "Infinity Quest"

    rendered = render_template(
        "visual.derive-visual-anchor",
        vars={"anchor_mode": "scene", "scene": mock_scene},
    )

    assert "The Starlight Nomad, a deep-space survey vessel." in rendered
    assert "science fiction" in rendered
    assert "concrete location type" in rendered
