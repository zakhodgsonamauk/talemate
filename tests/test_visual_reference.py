"""Tests for character reference attachment — pinning identity with the card image.

See docs/fork/visual-reference-consistency-design.md. Anchors made prompts stable but
not specific: a checkpoint samples a fresh person from "deep violet skin, indigo hair"
every time. Attaching the character's own card as a reference is what routes the request
to a reference-conditioned backend, so these tests are mostly about *when* that happens
and, just as importantly, when it must not.
"""

from types import SimpleNamespace

import pytest

from talemate.agents.visual.references import REFERENCE_TAG, ReferenceMixin
from talemate.agents.visual.schema import GEN_TYPE, VIS_TYPE, GenerationRequest
from talemate.context import active_scene

KAIRA_COVER = "a" * 64
ELMER_COVER = "b" * 64
KAIRA_CARD = "c" * 64
KAIRA_CARD_2 = "d" * 64


class FakeAgent(ReferenceMixin):
    """Minimal stand-in for the visual agent: only what the mixin actually reads."""

    def __init__(self, enabled=True, can_edit=True, max_references=1):
        self._enabled = enabled
        self._can_edit = can_edit
        self.backend_image_edit = SimpleNamespace(
            name="comfyui", max_references=max_references
        )

    def resolve_config(self, action, key):
        if action == "_references" and key == "enabled":
            return self._enabled
        raise KeyError(key)

    @property
    def can_edit_images(self):
        return self._can_edit


def make_scene(characters, covers=None, cards=None, tagged=None, owners=None):
    """A scene stub whose assets answer the questions the mixin asks."""
    covers = covers or {}
    cards = cards or {}
    tagged = tagged or {}
    # asset_id -> character_name (or None for unowned style/scene refs)
    owners = owners or {}

    def validate_asset_id(asset_id):
        return asset_id in covers.values()

    def search_assets(vis_type=None, character_name=None, tags=None, **kwargs):
        if tags:
            if REFERENCE_TAG not in tags:
                return []
            return tagged.get(character_name, [])
        if vis_type != VIS_TYPE.CHARACTER_CARD:
            return []
        return cards.get(character_name, [])

    def get_asset(asset_id):
        if asset_id not in owners:
            raise KeyError(asset_id)
        return SimpleNamespace(
            meta=SimpleNamespace(character_name=owners[asset_id])
        )

    return SimpleNamespace(
        characters=characters,
        assets=SimpleNamespace(
            validate_asset_id=validate_asset_id,
            search_assets=search_assets,
            get_asset=get_asset,
        ),
    )


def character(name, cover=None):
    return SimpleNamespace(name=name, cover_image=cover)


@pytest.fixture
def kaira():
    return character("Kaira", KAIRA_COVER)


@pytest.fixture
def illustration_request():
    return GenerationRequest(
        prompt=(
            "starship interior, science fiction, alien woman, deep violet skin, "
            "Kaira, standing at the console"
        ),
        vis_type=VIS_TYPE.SCENE_ILLUSTRATION,
    )


@pytest.fixture
def scene_ctx():
    """Sets and restores the active_scene contextvar the mixin reads."""
    tokens = []

    def _set(scene):
        tokens.append(active_scene.set(scene))
        return scene

    yield _set

    for token in reversed(tokens):
        try:
            active_scene.reset(token)
        except ValueError:
            # pytest-asyncio runs the coroutine in its own context, so the token was
            # created somewhere this teardown cannot reset. Harmless: every test sets
            # its own scene before reading it.
            pass


# ---------------------------------------------------------------------------
# T1 — the happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attaches_cover_image_and_routes_to_image_edit(
    kaira, illustration_request, scene_ctx
):
    scene_ctx(make_scene([kaira], covers={"Kaira": KAIRA_COVER}))
    agent = FakeAgent()

    await agent.attach_character_references(illustration_request)

    assert illustration_request.reference_assets == [KAIRA_COVER]
    # Setting references is what moves the request onto the edit backend; without this
    # the reference is carried but never used.
    assert illustration_request.gen_type == GEN_TYPE.IMAGE_EDIT


@pytest.mark.asyncio
async def test_falls_back_to_stored_character_card(illustration_request, scene_ctx):
    """A character whose cover was never set still has a card to anchor to."""
    scene_ctx(
        make_scene([character("Kaira", cover=None)], cards={"Kaira": [KAIRA_CARD]})
    )
    agent = FakeAgent()

    await agent.attach_character_references(illustration_request)

    assert illustration_request.reference_assets == [KAIRA_CARD]


# ---------------------------------------------------------------------------
# T2 — the bail-outs. Each of these must leave the request exactly as it was.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_config_attaches_nothing(kaira, illustration_request, scene_ctx):
    scene_ctx(make_scene([kaira], covers={"Kaira": KAIRA_COVER}))
    agent = FakeAgent(enabled=False)

    await agent.attach_character_references(illustration_request)

    assert illustration_request.reference_assets == []
    assert illustration_request.gen_type == GEN_TYPE.TEXT_TO_IMAGE


@pytest.mark.asyncio
async def test_inline_reference_is_not_overridden(
    kaira, illustration_request, scene_ctx
):
    """A user editing an image chose that reference deliberately."""
    scene_ctx(make_scene([kaira], covers={"Kaira": KAIRA_COVER}))
    illustration_request.inline_reference = "base64data"
    agent = FakeAgent()

    await agent.attach_character_references(illustration_request)

    assert illustration_request.reference_assets == []


@pytest.mark.asyncio
async def test_supplied_reference_matching_subject_is_kept(
    kaira, illustration_request, scene_ctx
):
    """The adjust flow's select_reference picked one of the subject's own cards."""
    scene_ctx(
        make_scene(
            [kaira],
            covers={"Kaira": KAIRA_COVER},
            owners={KAIRA_CARD: "Kaira"},
        )
    )
    illustration_request.reference_assets = [KAIRA_CARD]
    agent = FakeAgent()

    await agent.attach_character_references(illustration_request)

    assert illustration_request.reference_assets == [KAIRA_CARD]


@pytest.mark.asyncio
async def test_supplied_reference_for_wrong_character_is_replaced(
    kaira, illustration_request, scene_ctx
):
    """The observed live failure: select_reference free-matched the wrong
    character's card (Hannah's, with blank metadata) for a Kaira shot. Wrong-
    owner references are dropped and the subject's own cover attached."""
    hannah_card = "e" * 64
    scene_ctx(
        make_scene(
            [kaira, character("Hannah")],
            covers={"Kaira": KAIRA_COVER},
            owners={hannah_card: "Hannah"},
        )
    )
    illustration_request.reference_assets = [hannah_card]
    agent = FakeAgent()

    await agent.attach_character_references(illustration_request)

    assert illustration_request.reference_assets == [KAIRA_COVER]


@pytest.mark.asyncio
async def test_supplied_unowned_reference_is_kept(
    kaira, illustration_request, scene_ctx
):
    """Style/scene references with no character owner are legitimate."""
    style_ref = "f" * 64
    scene_ctx(
        make_scene(
            [kaira],
            covers={"Kaira": KAIRA_COVER},
            owners={style_ref: None},
        )
    )
    illustration_request.reference_assets = [style_ref]
    agent = FakeAgent()

    await agent.attach_character_references(illustration_request)

    assert illustration_request.reference_assets == [style_ref]


@pytest.mark.asyncio
async def test_supplied_unknown_reference_is_replaced(
    kaira, illustration_request, scene_ctx
):
    """An asset id that does not exist cannot be trusted - fall back to the subject."""
    scene_ctx(make_scene([kaira], covers={"Kaira": KAIRA_COVER}))
    illustration_request.reference_assets = ["deadbeef"]
    agent = FakeAgent()

    await agent.attach_character_references(illustration_request)

    assert illustration_request.reference_assets == [KAIRA_COVER]


@pytest.mark.asyncio
async def test_character_card_inherits_the_established_cover(kaira, scene_ctx):
    """A replacement card must look like the same character, not a fresh stranger.

    Cards generated without a reference came back as a different woman each time, which
    is why this vis type is referenced despite conditioning the card on its own cover.
    """
    scene_ctx(make_scene([kaira], covers={"Kaira": KAIRA_COVER}))
    request = GenerationRequest(
        prompt="Kaira, solo, character showcase", vis_type=VIS_TYPE.CHARACTER_CARD
    )
    agent = FakeAgent()

    await agent.attach_character_references(request)

    assert request.reference_assets == [KAIRA_COVER]
    assert request.gen_type == GEN_TYPE.IMAGE_EDIT


@pytest.mark.asyncio
async def test_character_name_selects_the_subject(scene_ctx):
    """A card request carries character_name; it outranks prompt mentions."""
    scene_ctx(
        make_scene(
            [character("Elmer", ELMER_COVER), character("Kaira", KAIRA_COVER)],
            covers={"Elmer": ELMER_COVER, "Kaira": KAIRA_COVER},
        )
    )
    request = GenerationRequest(
        # Prompt talks about Elmer far more; character_name must still win.
        prompt="Elmer, Elmer Farstield, console, Kaira",
        vis_type=VIS_TYPE.CHARACTER_CARD,
        character_name="Kaira",
    )
    agent = FakeAgent()

    await agent.attach_character_references(request)

    assert request.reference_assets == [KAIRA_COVER]


@pytest.mark.asyncio
async def test_no_edit_backend_attaches_nothing(kaira, illustration_request, scene_ctx):
    """Without a reference-capable backend an attached reference would be dropped."""
    scene_ctx(make_scene([kaira], covers={"Kaira": KAIRA_COVER}))
    agent = FakeAgent(can_edit=False)

    await agent.attach_character_references(illustration_request)

    assert illustration_request.reference_assets == []
    assert illustration_request.gen_type == GEN_TYPE.TEXT_TO_IMAGE


@pytest.mark.asyncio
async def test_zero_reference_slots_attaches_nothing(
    kaira, illustration_request, scene_ctx
):
    """A workflow with no 'Talemate Reference N' node reports max_references == 0."""
    scene_ctx(make_scene([kaira], covers={"Kaira": KAIRA_COVER}))
    agent = FakeAgent(max_references=0)

    await agent.attach_character_references(illustration_request)

    assert illustration_request.reference_assets == []


@pytest.mark.asyncio
async def test_character_absent_from_prompt_gets_no_reference(scene_ctx):
    """Same evidence as anchor pruning: the prompt names who is in the shot."""
    scene_ctx(
        make_scene(
            [character("Elmer", ELMER_COVER)], covers={"Elmer": ELMER_COVER}
        )
    )
    request = GenerationRequest(
        prompt="empty starship corridor, flickering lights",
        vis_type=VIS_TYPE.SCENE_ILLUSTRATION,
    )
    agent = FakeAgent()

    await agent.attach_character_references(request)

    assert request.reference_assets == []


@pytest.mark.asyncio
async def test_character_without_any_asset_attaches_nothing(
    illustration_request, scene_ctx
):
    scene_ctx(make_scene([character("Kaira", cover=None)]))
    agent = FakeAgent()

    await agent.attach_character_references(illustration_request)

    assert illustration_request.reference_assets == []
    assert illustration_request.gen_type == GEN_TYPE.TEXT_TO_IMAGE


# ---------------------------------------------------------------------------
# T3 — selection when more than one character is in frame
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_most_mentioned_character_wins_the_single_slot(scene_ctx):
    scene_ctx(
        make_scene(
            [character("Elmer", ELMER_COVER), character("Kaira", KAIRA_COVER)],
            covers={"Elmer": ELMER_COVER, "Kaira": KAIRA_COVER},
        )
    )
    request = GenerationRequest(
        # Kaira twice, Elmer once.
        prompt="Kaira, violet skin, Elmer, Kaira watching the console",
        vis_type=VIS_TYPE.SCENE_ILLUSTRATION,
    )
    agent = FakeAgent(max_references=1)

    await agent.attach_character_references(request)

    assert request.reference_assets == [KAIRA_COVER]


@pytest.mark.asyncio
async def test_extra_slots_take_more_images_of_the_same_subject(scene_ctx):
    """Identity averaging, not identity blending.

    Filling spare slots with a *second character* averages two people into a third. The
    slots take more pictures of the one subject instead.
    """
    scene_ctx(
        make_scene(
            [character("Elmer", ELMER_COVER), character("Kaira", KAIRA_COVER)],
            covers={"Elmer": ELMER_COVER, "Kaira": KAIRA_COVER},
            tagged={"Kaira": [KAIRA_CARD, KAIRA_CARD_2]},
        )
    )
    request = GenerationRequest(
        prompt="Kaira, violet skin, Elmer, Kaira watching the console",
        vis_type=VIS_TYPE.SCENE_ILLUSTRATION,
    )
    agent = FakeAgent(max_references=3)

    await agent.attach_character_references(request)

    assert request.reference_assets == [KAIRA_COVER, KAIRA_CARD, KAIRA_CARD_2]
    assert ELMER_COVER not in request.reference_assets


@pytest.mark.asyncio
async def test_untagged_cards_are_not_averaged_into_a_good_cover(kaira, scene_ctx):
    """Measured harm: blending off-spec cards washed out the subject's colouring.

    Extra references are opt-in via the tag. A character with a cover and a pile of
    mediocre cards must fall back to repeating the cover, not averaging the pile.
    """
    scene_ctx(
        make_scene(
            [kaira],
            covers={"Kaira": KAIRA_COVER},
            cards={"Kaira": [KAIRA_CARD, KAIRA_CARD_2]},
        )
    )
    request = GenerationRequest(
        prompt="Kaira at the console", vis_type=VIS_TYPE.SCENE_ILLUSTRATION
    )
    agent = FakeAgent(max_references=3)

    await agent.attach_character_references(request)

    assert request.reference_assets == [KAIRA_COVER] * 3


@pytest.mark.asyncio
async def test_slots_are_padded_when_the_subject_has_one_image(kaira, scene_ctx):
    """Every slot must be bound.

    An unpopulated reference node is disconnected by the backend, and a batch node whose
    input has been deleted fails ComfyUI validation - so a one-picture character would
    break the workflow rather than degrade.
    """
    scene_ctx(make_scene([kaira], covers={"Kaira": KAIRA_COVER}))
    request = GenerationRequest(
        prompt="Kaira at the console", vis_type=VIS_TYPE.SCENE_ILLUSTRATION
    )
    agent = FakeAgent(max_references=3)

    await agent.attach_character_references(request)

    assert request.reference_assets == [KAIRA_COVER] * 3


@pytest.mark.asyncio
async def test_reference_selection_is_stable_across_calls(scene_ctx):
    """An unstable pick would defeat the entire point: same shot, same reference."""
    scene = make_scene(
        [character("Elmer", ELMER_COVER), character("Kaira", KAIRA_COVER)],
        covers={"Elmer": ELMER_COVER, "Kaira": KAIRA_COVER},
    )
    scene_ctx(scene)
    agent = FakeAgent(max_references=1)

    picks = []
    for _ in range(5):
        request = GenerationRequest(
            # Equal mention counts: order must come from the cast, not from chance.
            prompt="Kaira and Elmer at the console",
            vis_type=VIS_TYPE.SCENE_ILLUSTRATION,
        )
        await agent.attach_character_references(request)
        picks.append(tuple(request.reference_assets))

    assert len(set(picks)) == 1


# ---------------------------------------------------------------------------
# T4 — subject selection when the prompt has no names in it
# ---------------------------------------------------------------------------


def character_with_anchor(name, cover, anchor):
    return SimpleNamespace(name=name, cover_image=cover, visual_anchor=anchor)


@pytest.mark.asyncio
async def test_anchor_tokens_identify_the_subject_when_no_name_survives(scene_ctx):
    """Observed live: the budget trimmed every name out of the prompt.

    What was left was style tags, one character's anchor and "nude, standing". Anchors are
    inserted by us and protected by the budget, so they are the more reliable evidence.
    """
    elmer = character_with_anchor(
        "Elmer",
        ELMER_COVER,
        "male, human, olive skin with some weathering, dark brown hair grey at temples, "
        "brown eyes, broad-shouldered build",
    )
    kaira = character_with_anchor(
        "Kaira",
        KAIRA_COVER,
        "violet skin, geometric facial markings, indigo hair, large black eyes",
    )
    scene_ctx(
        make_scene(
            [elmer, kaira], covers={"Elmer": ELMER_COVER, "Kaira": KAIRA_COVER}
        )
    )
    request = GenerationRequest(
        prompt=(
            "score_9, semi-realistic, cinematic lighting, (male, human, olive skin with "
            "some weathering, dark brown hair grey at temples, brown eyes, "
            "broad-shouldered build:1.3), nude, standing"
        ),
        vis_type=VIS_TYPE.SCENE_ILLUSTRATION,
    )
    agent = FakeAgent(max_references=1)

    await agent.attach_character_references(request)

    assert request.reference_assets == [ELMER_COVER]
    assert request.gen_type == GEN_TYPE.IMAGE_EDIT


@pytest.mark.asyncio
async def test_a_single_shared_anchor_word_does_not_pick_a_subject(scene_ctx):
    """The bar is a majority of the anchor, so "human" alone must not match."""
    elmer = character_with_anchor(
        "Elmer", ELMER_COVER, "male, human, olive skin, dark brown hair, brown eyes"
    )
    scene_ctx(make_scene([elmer], covers={"Elmer": ELMER_COVER}))
    request = GenerationRequest(
        prompt="starship corridor, human presence implied, flickering lights",
        vis_type=VIS_TYPE.SCENE_ILLUSTRATION,
    )
    agent = FakeAgent(max_references=1)

    await agent.attach_character_references(request)

    assert request.reference_assets == []


@pytest.mark.asyncio
async def test_a_face_hidden_character_is_skipped_for_the_slot(scene_ctx):
    """Elmer's rule renders his face in shadow, so his cover is a silhouette.

    Conditioning on it transfers no identity and darkens the frame. On a tie the slot must
    go to the character who actually has a face in their reference.
    """
    elmer = SimpleNamespace(
        name="Elmer",
        cover_image=ELMER_COVER,
        visual_rules="always has the head / face rendered completely in shadows",
    )
    kaira = SimpleNamespace(name="Kaira", cover_image=KAIRA_COVER, visual_rules=None)
    scene_ctx(
        make_scene(
            [elmer, kaira], covers={"Elmer": ELMER_COVER, "Kaira": KAIRA_COVER}
        )
    )
    request = GenerationRequest(
        prompt="Elmer at the console, Kaira watching",
        vis_type=VIS_TYPE.SCENE_ILLUSTRATION,
    )
    agent = FakeAgent(max_references=1)

    await agent.attach_character_references(request)

    assert request.reference_assets == [KAIRA_COVER]


@pytest.mark.asyncio
async def test_explicit_character_name_still_wins_over_the_face_rule(scene_ctx):
    """Asking for Elmer's own card means inheriting his silhouette on purpose."""
    elmer = SimpleNamespace(
        name="Elmer",
        cover_image=ELMER_COVER,
        visual_rules="face rendered completely in shadows",
    )
    scene_ctx(make_scene([elmer], covers={"Elmer": ELMER_COVER}))
    request = GenerationRequest(
        prompt="character showcase",
        vis_type=VIS_TYPE.CHARACTER_CARD,
        character_name="Elmer",
    )
    agent = FakeAgent(max_references=1)

    await agent.attach_character_references(request)

    assert request.reference_assets == [ELMER_COVER]


@pytest.mark.asyncio
async def test_explicit_empty_refs_are_honored(kaira, illustration_request, scene_ctx):
    """The confirmed live bug: removing the character reference in the modal
    was overridden by auto-attach. auto_references=False makes the caller's
    (empty) list authoritative."""
    scene_ctx(make_scene([kaira], covers={"Kaira": KAIRA_COVER}))
    illustration_request.auto_references = False
    agent = FakeAgent()

    await agent.attach_character_references(illustration_request)

    assert illustration_request.reference_assets == []
    assert illustration_request.gen_type == GEN_TYPE.TEXT_TO_IMAGE


@pytest.mark.asyncio
async def test_explicit_refs_still_validated(kaira, illustration_request, scene_ctx):
    """auto_references=False still validates a supplied list against the subject."""
    hannah_card = "9" * 64
    scene_ctx(
        make_scene(
            [kaira, character("Hannah")],
            covers={"Kaira": KAIRA_COVER},
            owners={hannah_card: "Hannah", KAIRA_CARD: "Kaira"},
        )
    )
    illustration_request.auto_references = False
    illustration_request.reference_assets = [hannah_card, KAIRA_CARD]
    agent = FakeAgent()

    await agent.attach_character_references(illustration_request)

    assert illustration_request.reference_assets == [KAIRA_CARD]


@pytest.mark.asyncio
async def test_explicit_all_wrong_refs_do_not_resurrect_cover(
    kaira, illustration_request, scene_ctx
):
    """With auto_references=False, dropping every supplied (wrong-subject)
    reference must not fall back to the subject's cover."""
    hannah_card = "9" * 64
    scene_ctx(
        make_scene(
            [kaira, character("Hannah")],
            covers={"Kaira": KAIRA_COVER},
            owners={hannah_card: "Hannah"},
        )
    )
    illustration_request.auto_references = False
    illustration_request.reference_assets = [hannah_card]
    agent = FakeAgent()

    await agent.attach_character_references(illustration_request)

    assert illustration_request.reference_assets == []
