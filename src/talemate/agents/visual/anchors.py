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

import hashlib
import re

import structlog

from talemate.agents.base import set_processing
from talemate.prompts import Prompt
from talemate.prompts.response import AnchorExtractor, ResponseSpec

__all__ = [
    "AnchorMixin",
    "ANCHOR_SPEC",
    "MAX_CHARACTERS_IN_FRAME",
    "WARDROBE_MARKERS",
    "WARDROBE_QUESTION",
    "characters_in_frame",
    "normalize_anchor",
    "strip_wardrobe_tokens",
]

# The reinforcement that keeps wardrobe current. Phrased as one question because
# reinforcements are keyed by question text - changing this string orphans existing ones.
WARDROBE_QUESTION = (
    "What is this character currently wearing, and what visible physical condition "
    "are they in?"
)

WARDROBE_INSTRUCTIONS = (
    "Answer in one or two plain sentences describing only clothing, footwear, visible "
    "equipment, and visible physical condition such as dirt, blood, injuries or "
    "dishevelment. If they are undressed, say so. Do not describe their permanent "
    "appearance, personality, or what they are doing."
)

DEFAULT_WARDROBE_INTERVAL = 10

log = structlog.get_logger("talemate.agents.visual.anchors")

# A safety valve, not a preference. Four full appearance anchors push the action
# keywords past what SDXL's 77-token chunk will attend to, so the thing the image is
# actually meant to depict gets ignored.
MAX_CHARACTERS_IN_FRAME = 3

ANCHOR_SPEC = ResponseSpec(
    extractors={
        "anchor": AnchorExtractor(left="<ANCHOR>", right="</ANCHOR>"),
    },
    required=[],
)


# Words that make a keyword about clothing or worn equipment rather than about the body.
# Used two ways: migrating clothing out of identity anchors cached by the previous track,
# and deciding whether the scene has already spoken about what someone is wearing.
#
# Kept to garments and worn things. Body words that sound adjacent - "bare shoulders",
# "broad-shouldered" - must not match, or migration would quietly delete real identity
# detail.
WARDROBE_MARKERS = {
    "suit",
    "uniform",
    "armour",
    "armor",
    "jacket",
    "coat",
    "cloak",
    "cape",
    "robe",
    "dress",
    "gown",
    "shirt",
    "blouse",
    "tunic",
    "vest",
    "trousers",
    "pants",
    "jeans",
    "skirt",
    "shorts",
    "leggings",
    "boots",
    "shoes",
    "sandals",
    "footwear",
    "gloves",
    "gauntlets",
    "helmet",
    "hat",
    "hood",
    "mask",
    "belt",
    "harness",
    "holster",
    "backpack",
    "pockets",
    "collar",
    "sleeves",
    "clothing",
    "clothes",
    "outfit",
    "attire",
    "garment",
    "garments",
    "naked",
    "nude",
    "undressed",
    "barefoot",
    "shirtless",
    "topless",
    "half-naked",
}


def _is_wardrobe_token(token: str) -> bool:
    """Whether a keyword is about clothing or worn equipment."""
    words = re.findall(r"[\w'-]+", token.lower())
    return any(word in WARDROBE_MARKERS for word in words)


def strip_wardrobe_tokens(anchor: str | None) -> str | None:
    """
    Remove clothing keywords from an identity anchor.

    A one-time migration for anchors cached by the previous track, whose derivation
    template asked for "the clothing they habitually wear". Those anchors now contradict
    the scene - one live prompt held both "fitted dark blue-grey utility suit" and
    "naked". Stripping in place costs no LLM call and is idempotent.
    """
    if not anchor:
        return anchor

    tokens = [token.strip() for token in anchor.split(",") if token.strip()]
    kept = [token for token in tokens if not _is_wardrobe_token(token)]

    if len(kept) == len(tokens):
        return anchor

    log.debug(
        "strip_wardrobe_tokens",
        removed=[token for token in tokens if _is_wardrobe_token(token)],
    )
    return ", ".join(kept)


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


# Rank and honorific words that are part of a character's stored name but are not
# identifying on their own. "Captain" would otherwise match every mention of any
# captain in the prose.
NAME_TITLES = {
    "captain",
    "commander",
    "lieutenant",
    "sergeant",
    "officer",
    "doctor",
    "dr",
    "dr.",
    "professor",
    "prof",
    "mr",
    "mr.",
    "mrs",
    "mrs.",
    "ms",
    "ms.",
    "miss",
    "sir",
    "lord",
    "lady",
    "the",
}


def _mention_count(text: str, name: str) -> int:
    """
    How many times `name` is referred to in `text`.

    Matches the full name and each of its identifying parts, because narration says
    "Elmer" far more often than "Captain Elmer Farstield". Titles are excluded as
    candidates so a stored name like "Captain Elmer Farstield" does not match every
    other captain in the prose.

    Word boundaries keep "Kai" from matching inside "Kaira". re.escape keeps
    apostrophes and spaces literal - character names contain both.
    """
    candidates = {name}
    for part in name.split():
        if len(part) > 2 and part.lower() not in NAME_TITLES:
            candidates.add(part)

    count = 0
    for candidate in candidates:
        pattern = rf"(?<!\w){re.escape(candidate)}(?!\w)"
        count += len(re.findall(pattern, text, flags=re.IGNORECASE))
    return count


def characters_in_frame(prompt, characters: list) -> list:
    """
    The characters actually visible in the shot, most-mentioned first.

    Read from the LLM's descriptive prose, which names who is present. That prose is
    generated even in KEYWORDS mode (graph node 8cfcb710 sets both
    positive_keywords_raw and positive_descriptive) - it is simply unused downstream,
    so reading it costs nothing.

    Falls back to every supplied character when there is no prose to read, because
    anchoring everyone is a smaller error than anchoring nobody.
    """
    characters = list(characters)
    if not characters:
        return []

    descriptive = " ".join(
        part.positive_descriptive for part in prompt.parts if part.positive_descriptive
    ).strip()

    if not descriptive:
        log.debug("characters_in_frame.no_descriptive", fallback="all characters")
        return characters[:MAX_CHARACTERS_IN_FRAME]

    scored = [
        (character, _mention_count(descriptive, character.name))
        for character in characters
    ]
    matched = [(character, count) for character, count in scored if count]

    # Sort by mentions, descending. Python's sort is stable, so equal counts keep
    # scene order rather than shuffling between generations - which matters, because an
    # unstable order would defeat the byte-stability this whole track is chasing.
    matched.sort(key=lambda pair: pair[1], reverse=True)

    if len(matched) > MAX_CHARACTERS_IN_FRAME:
        dropped = [character.name for character, _ in matched[MAX_CHARACTERS_IN_FRAME:]]
        log.warning(
            "characters_in_frame.capped",
            kept=MAX_CHARACTERS_IN_FRAME,
            dropped=dropped,
        )
        matched = matched[:MAX_CHARACTERS_IN_FRAME]

    return [character for character, _ in matched]


class AnchorMixin:
    """
    Derive-and-cache access to character and scene visual anchors.

    Mixed into the visual agent, which supplies `self.client`.
    """

    @set_processing
    async def _derive_anchor(self, anchor_mode: str, **vars) -> str | None:
        """
        Ask the LLM for an anchor.

        set_processing is not decoration for its own sake: it establishes the
        ActiveAgent context that the prompt machinery reads. Without it this raises
        AttributeError on a None context when called from a websocket handler, where no
        agent context exists yet.
        """
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

    async def wardrobe_anchor(self, character, report: str | None) -> str | None:
        """
        Keywords for what `character` is wearing right now, cached on the character.

        `report` is the world-state reinforcement's answer. It is re-queried on a cadence
        whether or not anything changed, so the report is hashed and a derivation only
        runs when it actually moved - otherwise every refresh would cost an LLM call for
        an identical result.
        """
        if not report or not report.strip():
            return character.visual_wardrobe

        fingerprint = hashlib.sha256(report.strip().encode("utf-8")).hexdigest()[:16]

        if character.visual_wardrobe and fingerprint == getattr(
            character, "_wardrobe_fingerprint", None
        ):
            return character.visual_wardrobe

        wardrobe = await self._derive_anchor(
            "wardrobe", character=character, wardrobe_report=report
        )

        if not wardrobe:
            log.warning("wardrobe_anchor.derivation_empty", character=character.name)
            return character.visual_wardrobe

        character.visual_wardrobe = wardrobe
        character._wardrobe_fingerprint = fingerprint
        character.memory_dirty = True
        log.info("wardrobe_anchor.derived", character=character.name, wardrobe=wardrobe)
        return wardrobe

    async def refresh_wardrobe(self, scene, character) -> str | None:
        """
        Current wardrobe keywords for `character`, refreshing from the reinforcement.

        Called while assembling an image prompt. Lazy on purpose: the reinforcement is
        kept current by the world-state agent on the story's own cadence, and this only
        reads whatever answer exists and converts it when it has changed. No background
        work, no LLM call in the common case.

        Returns the cached wardrobe unchanged when the feature is off, so switching it off
        stops new derivations without discarding what was already learned.
        """
        if not self._freshness_enabled():
            return character.visual_wardrobe

        try:
            await self.ensure_wardrobe_reinforcement(scene, character)
            _, reinforcement = await scene.world_state.find_reinforcement(
                WARDROBE_QUESTION, character.name
            )
        except Exception as e:
            # Never fail an image because world state misbehaved.
            log.warning(
                "refresh_wardrobe.reinforcement_unavailable",
                character=character.name,
                error=str(e),
            )
            return character.visual_wardrobe

        if not reinforcement or not reinforcement.answer:
            return character.visual_wardrobe

        return await self.wardrobe_anchor(character, reinforcement.answer)

    def _freshness_enabled(self) -> bool:
        try:
            value = self.resolve_config("_freshness", "enabled")
        except Exception:
            return True
        return True if value is None else bool(value)

    async def ensure_wardrobe_reinforcement(self, scene, character) -> None:
        """
        Make sure the wardrobe reinforcement exists for this character.

        Idempotent: reinforcements persist with the scene, so this must not stack
        duplicates across reloads. `insert="never"` keeps the answer out of story
        context - the image prompt consumes it, the narrative does not - which also
        avoids the history churn that the sequential insert mode causes.
        """
        _, existing = await scene.world_state.find_reinforcement(
            WARDROBE_QUESTION, character.name
        )
        if existing:
            return

        await scene.world_state.add_reinforcement(
            WARDROBE_QUESTION,
            character.name,
            WARDROBE_INSTRUCTIONS,
            self._wardrobe_interval(),
            "",
            "never",
            True,
        )
        log.info("wardrobe_reinforcement.created", character=character.name)

    def _wardrobe_interval(self) -> int:
        try:
            configured = self.resolve_config("_freshness", "wardrobe_interval")
        except Exception:
            configured = None
        return configured or DEFAULT_WARDROBE_INTERVAL

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
