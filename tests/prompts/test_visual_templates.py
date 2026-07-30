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

from pathlib import Path

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


# ---------------------------------------------------------------------------
# generate-image-SCENE_ILLUSTRATION - source-level assertions
#
# The template needs batch_query_scene, which needs a live scene and agents. These
# assertions read the template source instead: they are guarding against the exact
# wording being reintroduced, which is a source-level property.
# ---------------------------------------------------------------------------

SCENE_ILLUSTRATION_TEMPLATE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "talemate"
    / "prompts"
    / "templates"
    / "visual"
    / "generate-image-SCENE_ILLUSTRATION.jinja2"
)

PROMPT_TYPE_TEMPLATE = (
    SCENE_ILLUSTRATION_TEMPLATE.parent / "generate-image-prompt-type.jinja2"
)


def test_scene_illustration_no_longer_queries_appearance():
    """The per-character appearance query is what made every image a different person,
    and it cost one LLM call per character per image."""
    source = SCENE_ILLUSTRATION_TEMPLATE.read_text(encoding="utf-8")

    assert "physical appearance" not in source
    assert "what clothes" not in source
    assert 'queries.append({"id": "char_' not in source


def test_scene_illustration_no_longer_asks_the_llm_to_emphasise_format():
    """RC3. This instruction is why "horizontal, landscape, cinematic, dynamic" kept
    landing in the image prompt, where the resolution had already settled it."""
    source = SCENE_ILLUSTRATION_TEMPLATE.read_text(encoding="utf-8")

    assert "emphasizes the horizontal/landscape format" not in source
    assert "Portrait orientation (must be horizontal/landscape)" not in source
    assert "Square format compositions" not in source


def test_scene_illustration_demands_the_setting_be_named():
    """RC2. The sampled prompt contained no setting token at all, so a starship control
    room was rendered with mountains outside the windows."""
    source = SCENE_ILLUSTRATION_TEMPLATE.read_text(encoding="utf-8")

    assert "Name the setting" in source
    assert "concrete location and its genre" in source


def test_scene_illustration_bans_the_observed_junk_vocabulary():
    source = SCENE_ILLUSTRATION_TEMPLATE.read_text(encoding="utf-8")

    assert "Plot or state words" in source
    assert "render as nothing at all" in source
    assert "Character appearance" in source


def test_prompt_type_requires_photographable_keywords():
    source = PROMPT_TYPE_TEMPLATE.read_text(encoding="utf-8")

    assert "a camera could photograph" in source
    assert "will be discarded before generation" in source


# ---------------------------------------------------------------------------
# derive-visual-anchor - identity/wardrobe split
#
# Clothing used to live in the identity anchor, which meant a cached "utility suit"
# argued with a scene that said "naked". Identity is permanent; clothing is not.
# ---------------------------------------------------------------------------


def test_derive_anchor_character_mode_excludes_clothing(kaira):
    rendered = render_template(
        "visual.derive-visual-anchor",
        vars={"anchor_mode": "character", "character": kaira},
    )

    assert "Exclude clothing entirely" in rendered
    assert "the clothing they habitually wear" not in rendered
    # The worked example must not model the behaviour we just banned.
    assert "utility suit</ANCHOR>" not in rendered


def test_derive_anchor_wardrobe_mode_renders(kaira):
    rendered = render_template(
        "visual.derive-visual-anchor",
        vars={
            "anchor_mode": "wardrobe",
            "character": kaira,
            "wardrobe_report": "She has removed her EVA suit and stands barefoot.",
        },
    )

    assert "She has removed her EVA suit and stands barefoot." in rendered
    assert "wearing right now" in rendered
    assert "Exclude their permanent appearance" in rendered


def test_derive_anchor_wardrobe_mode_allows_present_tense(kaira):
    """Wardrobe is explicitly about now, so the "nothing about what they are doing right
    now" rule must not apply to it - unlike identity mode."""
    identity = render_template(
        "visual.derive-visual-anchor",
        vars={"anchor_mode": "character", "character": kaira},
    )
    wardrobe = render_template(
        "visual.derive-visual-anchor",
        vars={
            "anchor_mode": "wardrobe",
            "character": kaira,
            "wardrobe_report": "Wearing a torn jacket.",
        },
    )

    assert "or is doing right now" in identity
    assert "or is doing right now" not in wardrobe


def test_derive_anchor_scene_mode_includes_location_when_known(mock_scene):
    mock_scene.description = "The Starlight Nomad."
    mock_scene.context = "science fiction"
    mock_scene.title = "Infinity Quest"

    rendered = render_template(
        "visual.derive-visual-anchor",
        vars={
            "anchor_mode": "scene",
            "scene": mock_scene,
            "location": "the derelict structure's interior",
        },
    )

    assert "the derelict structure's interior" in rendered
