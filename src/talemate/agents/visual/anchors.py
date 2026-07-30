"""
Visual anchors: the fixed keyword lists that keep generated images consistent.

An anchor describes what a subject permanently looks like - "deep violet skin, indigo
hair pulled back, fitted dark blue-grey utility suit". It is derived once from canonical
prose, cached on the subject, and from then on injected verbatim into every image prompt
featuring it.

The caching is the load-bearing part. Appearance used to be re-derived by the LLM on
every generation via a batch_query_scene call, and a non-deterministic source cannot
produce a stable subject - which is why the same character arrived as a different person
in every illustration. See docs/fork/visual-consistency-design.md.

Derivation happens here, in Python, rather than in the generation node graph, because it
runs once per subject rather than once per image.
"""

import structlog

from talemate.prompts import Prompt
from talemate.prompts.response import AnchorExtractor, ResponseSpec

__all__ = [
    "AnchorMixin",
    "ANCHOR_SPEC",
    "normalize_anchor",
]

log = structlog.get_logger("talemate.agents.visual.anchors")

ANCHOR_SPEC = ResponseSpec(
    extractors={
        "anchor": AnchorExtractor(left="<ANCHOR>", right="</ANCHOR>"),
    },
    required=[],
)


def normalize_anchor(raw: str | None) -> str | None:
    """
    Turn whatever the LLM said into a clean, comma-space delimited keyword list.

    Strips any leftover anchor tags, drops empty tokens, and dedupes while preserving
    order. Returns None for anything that boils down to nothing, so callers can tell
    "derivation failed" from "derived an empty string" - the former must not be cached.
    """
    if not raw:
        return None

    text = raw.replace("<ANCHOR>", "").replace("</ANCHOR>", "").strip()
    if not text:
        return None

    # Newlines are a common LLM habit despite asking for one line.
    text = text.replace("\n", ",")

    tokens = [token.strip() for token in text.split(",")]
    tokens = [token for token in tokens if token]

    # dict.fromkeys dedupes while keeping first-seen order.
    tokens = list(dict.fromkeys(tokens))

    if not tokens:
        return None

    return ", ".join(tokens)


class AnchorMixin:
    """
    Derive-and-cache access to character and scene visual anchors.

    Mixed into the visual agent, which supplies `self.client`.
    """

    async def _derive_anchor(self, anchor_mode: str, **vars) -> str | None:
        _, extracted = await Prompt.request(
            "visual.derive-visual-anchor",
            self.client,
            # "visualize" is the visual agent's own kind (150 tokens). "create_short"
            # caps at 25, which truncates a 14-keyword list mid-word.
            "visualize",
            vars={"anchor_mode": anchor_mode, **vars},
            response_spec=ANCHOR_SPEC,
        )
        return normalize_anchor(extracted.get("anchor"))

    async def character_anchor(self, character) -> str | None:
        """
        The appearance keywords for `character`, cached on the character.

        Returns None when there is nothing to work from - a character with neither an
        appearance attribute nor a description gets no anchor rather than an invented
        one.
        """
        if character.visual_anchor:
            return character.visual_anchor

        appearance = (character.base_attributes or {}).get("appearance")
        if not appearance and not character.description:
            log.debug(
                "character_anchor.no_source_material", character=character.name
            )
            return None

        anchor = await self._derive_anchor("character", character=character)

        if not anchor:
            log.warning("character_anchor.derivation_empty", character=character.name)
            return None

        character.visual_anchor = anchor
        character.memory_dirty = True
        log.info(
            "character_anchor.derived", character=character.name, anchor=anchor
        )
        return anchor

    async def scene_anchor(self, scene) -> str | None:
        """
        The setting keywords for `scene`, cached on the scene.

        Without this the keyword prompt kept losing the setting entirely, which is how a
        starship control room ended up with mountains outside the windows.
        """
        if scene.visual_anchor:
            return scene.visual_anchor

        if not scene.description:
            log.debug("scene_anchor.no_source_material")
            return None

        anchor = await self._derive_anchor("scene", scene=scene)

        if not anchor:
            log.warning("scene_anchor.derivation_empty")
            return None

        scene.visual_anchor = anchor
        log.info("scene_anchor.derived", anchor=anchor)
        return anchor
