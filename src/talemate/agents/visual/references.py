"""
Character reference images: pinning identity with pixels instead of words.

A visual anchor makes a prompt *stable* - the same keywords every time. It cannot make
it *specific*. "deep violet skin, indigo hair, large dark eyes" describes a type, and a
checkpoint samples a fresh person from that description on every generation, so
illustrations of the same character are still three different people. Evidence for that
is in docs/fork/visual-consistency-design.md, whose closing note says plainly that
prompt-assembly correctness is not rendering fidelity.

This module closes the gap by attaching the character's own card image to the request as
a reference. The generation then routes to the image-edit backend, where a
reference-conditioned workflow (IP-Adapter) constrains identity from the image itself.

Everything here is selection, not rendering: which character is in the shot, which of
their assets is canonical, and whether a reference-capable backend exists to receive it.
The conditioning strength lives in the ComfyUI workflow, not in Python.
"""

import re

import structlog

from talemate.context import active_scene

from .anchors import _mention_count
from .schema import GEN_TYPE, VIS_TYPE, GenerationRequest
from .style import VIS_TYPES_WITHOUT_CAST

__all__ = [
    "ReferenceMixin",
    "REFERENCE_VIS_TYPES",
]

log = structlog.get_logger("talemate.agents.visual.references")

# Which kinds of image get an automatic character reference.
#
# CHARACTER_CARD is included despite the obvious objection - that the card *is* the
# reference, so conditioning it on itself freezes whatever pose it holds. Measured
# behaviour settled it: cards generated without a reference came back as a different
# woman each time, pale where the prose says deep violet. Inheriting the established
# cover's framing is the smaller cost, and it is what makes a *replacement* card look
# like the same character.
REFERENCE_VIS_TYPES = {VIS_TYPE.SCENE_ILLUSTRATION, VIS_TYPE.CHARACTER_CARD}

# Asset tag that opts an image into the reference set.
#
# Averaging every stored card is actively harmful: measured on Kaira, blending her good
# cover with two off-spec cards washed the violet skin back towards human and lost her
# markings. So extra references are curated, never assumed - the cover alone (repeated
# across the workflow's slots) is the default, and tagging an image adds it deliberately.
REFERENCE_TAG = "reference"

# A visual rule that deliberately hides the face, which makes the character useless as an
# identity reference.
#
# Elmer's rule is "always has the head / face rendered completely in shadows", and his cover
# image obeys it: a silhouette. Conditioning a scene on that transfers no identity - there is
# no face in it - while still dragging the reference's darkness across the frame. Observed
# live: it produced an incoherent image with no setting at all.
_HIDDEN_FACE_RE = re.compile(
    r"\b(face|head)\b[^.]{0,50}\b(shadow|shadows|hidden|obscured|concealed|covered|masked)\b"
    r"|\b(shadow|shadows|hidden|obscured|concealed)\b[^.]{0,50}\b(face|head)\b",
    re.IGNORECASE,
)


class ReferenceMixin:
    """
    Attach character card images to a generation request as identity references.

    Mixed into the visual agent, which supplies `can_edit_images`, `backend_image_edit`
    and `resolve_config`.
    """

    def _references_enabled(self) -> bool:
        try:
            value = self.resolve_config("_references", "enabled")
        except Exception:
            return False
        return bool(value)

    def _reference_limit(self) -> int:
        """
        How many references the edit backend will accept.

        Read from the backend rather than configured here: for ComfyUI it is the number of
        "Talemate Reference N" nodes the selected workflow actually has, and sending more
        than that silently drops the extras.
        """
        backend = self.backend_image_edit
        limit = getattr(backend, "max_references", 0) or 0
        return max(0, int(limit))

    def _characters_in_prompt(self, request: GenerationRequest) -> list:
        """
        The scene's characters named in the assembled prompt, most-mentioned first.

        Same evidence as `_drop_absent_character_anchors`: by this point the prompt holds
        the LLM's keywords, which name whoever is in the shot. Using the same source means
        the character who keeps their anchor is the character who gets a reference.
        """
        scene = active_scene.get()
        if not scene:
            return []

        characters = list(getattr(self, "characters", None) or scene.characters)
        if not characters or not request.prompt:
            return []

        scored = [
            (character, _mention_count(request.prompt, character.name))
            for character in characters
        ]
        matched = [(character, count) for character, count in scored if count]

        # Stable ordering: sort is stable, so equal mention counts keep scene order and
        # the same character wins the single reference slot on every generation.
        matched.sort(key=lambda pair: pair[1], reverse=True)
        return [character for character, _ in matched]

    def _character_reference_assets(self, scene, character) -> list[str]:
        """
        Every asset id that shows what this character looks like, best first.

        Their cover image leads: a human chose it, and it is what the character sheet
        displays, so anchoring to anything else would contradict the UI. Images tagged
        `reference` follow, for callers who have curated a set worth averaging.

        Untagged cards are used *only* when there is no cover at all - see REFERENCE_TAG
        for why they are not blended in otherwise.

        Returns an empty list rather than guessing. An illustration with no reference is
        the old behaviour: a worse image, not a wrong one.
        """

        def search(**kwargs) -> list[str]:
            try:
                # Returns asset ids, not asset objects.
                return scene.assets.search_assets(**kwargs) or []
            except Exception as e:
                log.warning(
                    "character_reference_assets.search_failed",
                    character=character.name,
                    error=str(e),
                )
                return []

        found: list[str] = []

        cover = getattr(character, "cover_image", None)
        if cover and scene.assets.validate_asset_id(cover):
            found.append(cover)

        for asset_id in search(character_name=character.name, tags=[REFERENCE_TAG]):
            if asset_id and asset_id not in found:
                found.append(asset_id)

        if not found:
            # No cover and nothing curated: any stored card beats no reference at all.
            for asset_id in search(
                vis_type=VIS_TYPE.CHARACTER_CARD, character_name=character.name
            ):
                if asset_id and asset_id not in found:
                    found.append(asset_id)

        return found

    def _subject(self, request: GenerationRequest):
        """
        The one character this image is of.

        References go to a single subject rather than one per character in frame. Blending
        two people's embeddings produces a third person, and the measured failure mode of
        multiple references is exactly that. `character_name` is authoritative when set -
        a CHARACTER_CARD request carries it - otherwise the most-mentioned character in the
        prompt wins, which is the same evidence anchor pruning uses.
        """
        scene = active_scene.get()
        if not scene:
            return None

        characters = list(getattr(self, "characters", None) or scene.characters)

        if request.character_name:
            for character in characters:
                if character.name.lower() == request.character_name.lower():
                    return character

        in_prompt = [c for c in self._characters_in_prompt(request) if self._referenceable(c)]
        if in_prompt:
            return in_prompt[0]

        for candidate in self._subjects_by_anchor(request, characters):
            if self._referenceable(candidate):
                return candidate
        return None

    @staticmethod
    def _referenceable(character) -> bool:
        """
        Whether conditioning on this character's image can do any good.

        A rule that hides the face makes their reference a silhouette, which carries no
        identity to transfer and drags its own darkness into the frame. Skipping them lets
        the next character in the shot take the slot - which, on a tie, is the difference
        between anchoring to a faceless outline and anchoring to a real portrait.

        Only applies to automatic selection. An explicit `character_name` still wins: if a
        caller asks for that character's card, inheriting the silhouette is the point.
        """
        rules = getattr(character, "visual_rules", None)
        if rules and _HIDDEN_FACE_RE.search(rules):
            log.debug("referenceable.skipped_hidden_face", character=character.name)
            return False
        return True

    def _subjects_by_anchor(self, request: GenerationRequest, characters: list) -> list:
        """
        Fall back to matching the appearance keywords we inserted ourselves.

        Names reach the prompt only because the LLM happened to write them, and the token
        budget trims the LLM's keywords first - so a real generation can arrive here with
        no name in it at all. Observed live: the whole prompt was style tags, one
        character's anchor and "nude, standing".

        Anchors are different. We insert them, they are ordered ahead of the LLM's part,
        and the budget protects the first one, so whoever's anchor survives is the best
        available evidence of who the image is of. Only cached anchors are read - deriving
        one here would mean an LLM call inside subject selection.

        Returns every match, best first, so the caller can skip a character whose reference
        is unusable and still find the next one.
        """
        prompt = (request.prompt or "").lower()
        if not prompt:
            return []

        scored = []
        for character in characters:
            anchor = getattr(character, "visual_anchor", None)
            if not anchor:
                continue
            tokens = [t.strip().lower() for t in anchor.split(",") if len(t.strip()) > 3]
            if not tokens:
                continue
            score = sum(1 for token in tokens if token in prompt)
            # Majority of the anchor present, so a single shared word cannot win it.
            if score >= max(2, len(tokens) // 2):
                scored.append((score, character))

        # Sort by score only - sorted() is stable, so equal scores keep scene order and the
        # pick stays the same between generations.
        scored.sort(key=lambda pair: pair[0], reverse=True)

        if scored:
            log.debug(
                "subjects_by_anchor.matched",
                candidates=[(c.name, s) for s, c in scored],
            )
        return [character for _score, character in scored]

    @staticmethod
    def _fill_slots(assets: list[str], limit: int) -> list[str]:
        """
        Repeat the available references until every slot is populated.

        Not cosmetic. A workflow that batches its reference nodes needs all of them bound:
        `Workflow.set_reference_images` disconnects unpopulated ones, and a batch node whose
        input has been deleted fails ComfyUI validation. Repeating an image under
        `combine_embeds: average` simply weights it more heavily, which is the correct
        behaviour when a character only has one good picture.
        """
        if not assets:
            return []

        filled = [assets[i % len(assets)] for i in range(limit)]

        # Repeating one image is free - the average of identical embeddings is that
        # embedding. Repeating *some* of several is not: two images across three slots
        # weights the first two-thirds to one-third. Say so rather than skewing quietly.
        if 1 < len(assets) < limit:
            log.warning(
                "reference_slots.uneven_padding",
                distinct=len(assets),
                slots=limit,
                note="first reference is weighted more heavily; prefer a workflow whose "
                "reference-node count matches the curated set",
            )

        return filled

    async def attach_character_references(self, request: GenerationRequest) -> None:
        """
        Populate `reference_assets` with the in-frame characters' card images.

        Setting references is what routes the request to the edit backend - the same rule
        the SelectBackend node uses - so this must run before the prompt is finalised,
        which picks its keyword handling from whichever backend will receive it.

        Bails out silently and often. A missing backend, a caller-supplied reference, an
        unsupported vis type or a character with no card all mean "generate the way you
        did before", and none of them should cost an image.
        """
        if not self._references_enabled():
            return

        if request.inline_reference:
            # An explicit reference - a user editing an image - always wins.
            log.debug("attach_character_references.caller_supplied")
            return

        if request.vis_type not in REFERENCE_VIS_TYPES:
            return

        if request.vis_type in VIS_TYPES_WITHOUT_CAST:
            return

        if not self.can_edit_images:
            log.debug("attach_character_references.no_edit_backend")
            return

        limit = self._reference_limit()
        if not limit:
            log.warning(
                "attach_character_references.no_reference_slots",
                backend=getattr(self.backend_image_edit, "name", None),
            )
            return

        scene = active_scene.get()
        if not scene:
            return

        subject = self._subject(request)

        if request.reference_assets:
            # Pre-populated references come from the adjust flow's
            # select_reference LLM, which free-matches over the whole asset
            # library and has picked the wrong character off empty asset
            # metadata before (observed live: Hannah's blank card chosen for
            # a Kaira shot). Validate against the resolved subject instead of
            # trusting it; assets with no character owner (style/scene refs)
            # are kept.
            if not subject:
                log.debug("attach_character_references.caller_supplied")
                return

            kept: list[str] = []
            dropped: list[str] = []
            for asset_id in request.reference_assets:
                try:
                    owner = scene.assets.get_asset(asset_id).meta.character_name
                except Exception:
                    dropped.append(asset_id)
                    continue
                if owner and owner.lower() != subject.name.lower():
                    dropped.append(asset_id)
                else:
                    kept.append(asset_id)

            if dropped:
                log.info(
                    "attach_character_references.dropped_wrong_subject",
                    subject=subject.name,
                    dropped=[asset_id[:10] for asset_id in dropped],
                    kept=[asset_id[:10] for asset_id in kept],
                )

            if kept:
                request.reference_assets = kept
                return

            # every supplied reference belonged to someone else - fall
            # through and attach the subject's own cards instead
            request.reference_assets = []

        if not subject:
            log.debug("attach_character_references.no_subject")
            return

        available = self._character_reference_assets(scene, subject)
        if not available:
            log.debug("attach_character_references.no_asset", character=subject.name)
            return

        picked = self._fill_slots(available[:limit], limit)

        request.reference_assets = picked
        request.gen_type = GEN_TYPE.IMAGE_EDIT

        log.info(
            "attach_character_references.attached",
            vis_type=str(request.vis_type),
            character=subject.name,
            slots=limit,
            distinct=len(set(picked)),
            assets=[asset_id[:10] for asset_id in picked],
        )
