import dataclasses
import re

import structlog

from .anchors import characters_in_frame, condense_visual_rule
from .schema import VIS_TYPE, VisualPrompt, VisualPromptPart

from talemate.agents.base import AgentAction, AgentActionConfig, AgentActionNote
from talemate.world_state.manager import WorldStateManager
from talemate.world_state.templates import Collection
from talemate.world_state.templates.visual import VisualStyle

__all__ = [
    "StyleMixin",
]

log = structlog.get_logger("talemate.agents.visual.style")

# Vis types depicting a thing rather than a cast. Character anchors are meaningless
# here and actively harmful - anchoring the crew into an object study would put people
# in a picture of a sidearm.
VIS_TYPES_WITHOUT_CAST = {
    VIS_TYPE.OBJECT_ILLUSTRATION,
    VIS_TYPE.SCENE_BACKGROUND,
}

# Portraits of one person. Their style templates already say "solo", so counting other
# people present contradicts the style in the same prompt.
VIS_TYPES_SOLO = {
    VIS_TYPE.CHARACTER_CARD,
    VIS_TYPE.CHARACTER_PORTRAIT,
}


# SDXL's text encoder works in 77-token chunks; A1111 concatenates the embeddings of
# several, with attention thinning as they go.
#
# One chunk. Went 150 -> 250 to stop anchors starving the action, then back to 77 once a
# real prompt was read end to end: at 155 tokens roughly half of it was past the point
# where the encoder meaningfully attends, so the extra length bought nothing and made the
# prompt harder to reason about. Fitting one chunk means the prompt means what it says.
DEFAULT_MAX_PROMPT_TOKENS = 77

_WORD_RE = re.compile(r"[\w'-]+")


def estimate_prompt_tokens(text: str) -> int:
    """
    Rough CLIP token count.

    An estimate, not a tokenizer: one token per word plus one per comma separator.
    CLIP's BPE splits some words into several tokens, so this reads low on unusual
    vocabulary. Good enough to decide when to start trimming, and it avoids dragging a
    tokenizer dependency into the prompt path.
    """
    if not text:
        return 0
    return len(_WORD_RE.findall(text)) + text.count(",")


# Underscore-joined keywords the LLM sometimes produces: "starship_bridge",
# "moment_of_tension". Underscores are word characters, so every comparison downstream -
# blocklist, character-name matching, duplicate-setting overlap - silently misses them.
# Booru-style score tags are the deliberate exception; those must stay as written.
_KEEP_UNDERSCORES = re.compile(r"^(score_\d+(_up)?|source_\w+|rating_\w+)$", re.I)


def normalize_keyword(keyword: str) -> str:
    """Turn underscore-joined keywords into ordinary words, leaving score tags alone."""
    stripped = keyword.strip()
    if "_" not in stripped or _KEEP_UNDERSCORES.match(stripped):
        return stripped
    return stripped.replace("_", " ")


_ESCAPED_OPEN = "\x00ESCOPEN\x00"
_ESCAPED_CLOSE = "\x00ESCCLOSE\x00"
_EMPHASIS_WEIGHT = re.compile(r":\s*\d+(?:\.\d+)?\s*(?=\)|,|$)")


def strip_emphasis(prompt: str) -> str:
    """
    Remove A1111 emphasis syntax, leaving the keywords behind.

    Regenerate hands a previous request back to generation, so the prompt reaching
    _finalize_prompt may already be weighted. Without stripping first, emphasis is
    applied on top of emphasis and compounds every time - observed reaching three
    nested levels and an unbalanced bracket, which parses as something quite unlike
    what it looks like.

    Escaped brackets are literal content - a scar "\\(old\\)" - and are preserved.
    """
    if not prompt or ("(" not in prompt and ":" not in prompt):
        return prompt

    text = prompt.replace(r"\(", _ESCAPED_OPEN).replace(r"\)", _ESCAPED_CLOSE)
    text = _EMPHASIS_WEIGHT.sub("", text)
    text = text.replace("(", "").replace(")", "")
    text = text.replace(_ESCAPED_OPEN, r"\(").replace(_ESCAPED_CLOSE, r"\)")

    # Unwrapping can leave doubled separators behind.
    parts = [part.strip() for part in text.split(",")]
    return ", ".join(part for part in parts if part)


# A bracket that is not already backslash-escaped. Keeps weight_group idempotent;
# see its docstring for what compounding escapes did.
_UNESCAPED_BRACKET = re.compile(r"(?<!\\)[()]")


def weight_group(keywords: list[str], weight: float) -> str:
    """
    Render keywords as an A1111 emphasis group: `(a, b, c:1.3)`.

    Applied to the identity group as a whole rather than per keyword, which would spend a
    bracket pair on each. Emphasis is the prompt-level answer to the model ignoring a
    token - "deep violet skin" came back near-human at default weight.

    Existing brackets are escaped; an unescaped one would change how the rest of the
    prompt parses.

    Only brackets that are not already escaped. strip_emphasis leaves `\\(` intact
    because it is literal content, so escaping unconditionally compounded the
    backslashes on every finalise pass - `\\(old\\)` becoming `\\\\(old\\\\)`, which is
    a literal backslash followed by an unescaped bracket, i.e. the very thing this
    escaping exists to prevent. Reachable from regenerate and from prompt
    adjustment, both of which finalise a prompt that was already finalised once.
    """
    text = ", ".join(keywords)
    if not text:
        return text

    text = _UNESCAPED_BRACKET.sub(r"\\\g<0>", text)

    if abs(weight - 1.0) < 0.01:
        return ", ".join(keywords)

    return f"({text}:{weight:g})"


def split_anchor(anchor: str) -> list[str]:
    """Comma-delimited anchor string to keyword list, blanks dropped."""
    return [token.strip() for token in anchor.split(",") if token.strip()]


@dataclasses.dataclass
class AnchorParts:
    """
    The prompt parts _insert_anchors created, kept apart by role.

    The budget enforcer needs to know which part is which to drop them in the right
    order, and a flat list cannot tell a scene anchor from a character one.
    """

    scene: VisualPromptPart | None = None
    characters: list[VisualPromptPart] = dataclasses.field(default_factory=list)

    @property
    def ordered(self) -> list[VisualPromptPart]:
        """Insertion order: scene anchor first, then one part per character."""
        parts = [self.scene] if self.scene else []
        return parts + self.characters


# Keywords the prompt-writing LLM emits that a diffusion model cannot act on. Matched
# whole-token and case-insensitively, so "traction control" survives while "action" does
# not.
#
# Format and camera meta. The LLM produces these because our own templates used to ask
# it to "emphasize the horizontal/landscape format". Orientation is decided by the
# resolution, so restating it in the prompt only spends tokens.
BANNED_FORMAT_KEYWORDS = {
    "horizontal",
    "vertical",
    "landscape",
    "portrait",
    "portrait format",
    "landscape format",
    # Observed after the earlier entries shipped - the same instruction, differently
    # worded. Orientation comes from the configured resolution, and "solo" already comes
    # from the character-card style template.
    "portrait orientation",
    "landscape orientation",
    "orientation",
    "single subject",
    "one subject",
    "solo subject",
    "horizontal/landscape",
    "horizontal composition",
    "square",
    "square format",
    "cinematic",
    "cinematic framing",
    "cinematic composition",
    "dynamic",
    "dynamic composition",
    "dynamic moment",
    "dynamic space scene",
    "composition",
    "framing",
    "moment-capturing",
    "screen cap",
    "screencap",
    "movie still",
    "storyboard",
    "storyboard frame",
    "wide shot",
    "aspect ratio",
}

# Plot, state and mood words. They read as meaningful to a person and render as nothing:
# "corruption" and "diagnostic" contributed exactly zero pixels to the image that
# started this track.
BANNED_ABSTRACT_KEYWORDS = {
    "action",
    "interaction",
    "characters in action",
    "emotion",
    "tension",
    "tense",
    "tense moment",
    "tense atmosphere",
    "atmosphere",
    "moment",
    "key moment",
    "pivotal moment",
    "frozen moment",
    "corruption",
    "corrupted data",
    "diagnostic",
    "diagnostics",
    "waiting",
    "watching",
    "focused",
    "focus",
    "emergency protocols",
    "drama",
    "dramatic",
    "mood",
    "environment",
    "setting",
    "surroundings",
    "background",
    "narrative",
    "storytelling",
    "visual storytelling",
    # Second wave, observed in live generations after the first pass shipped. The LLM
    # names the *category* of a visual detail instead of the detail: "posture" where it
    # should say "shoulders hunched", "lighting" where it should say "lit from below".
    "posture",
    "expression",
    "expressions",
    "movement",
    "physical actions",
    "lighting",
    "specific moment",
    "frustration",
    "realization",
    "determination",
    "concentration",
    "concern",
    "urgency",
    "unease",
    "dread",
    "awe",
    # Rendering words the LLM adds on its own. Deliberately only ones the style
    # templates do not themselves emit - the sanitiser runs over the whole assembled
    # prompt, so banning "semi-realistic" or "masterpiece" here would strip the
    # configured art style's own tags.
    "painterly",
    "rendered finish",
    "painterly finish",
    # Third wave: genre and plot abstractions. These describe the story someone is in,
    # not anything visible in the frame.
    "mystery",
    "investigation",
    "problem-solving",
    "adventure",
    "exploration",
    "discovery",
    "sci-fi setting",
    "science fiction setting",
    "fantasy setting",
    "elite crew",
    "space exploration",
}

BANNED_KEYWORDS = BANNED_FORMAT_KEYWORDS | BANNED_ABSTRACT_KEYWORDS


def sanitise_keywords(keywords: list[str]) -> list[str]:
    """
    Drop keywords a diffusion model cannot render.

    Whole-token matching only. Applied to the LLM's keyword list and never to anchors or
    style templates, which we author and which are already clean.
    """
    kept: list[str] = []
    dropped: list[str] = []

    for keyword in keywords:
        if keyword.strip().lower() in BANNED_KEYWORDS:
            dropped.append(keyword)
        else:
            kept.append(keyword)

    if dropped:
        log.debug("sanitise_keywords.dropped", keywords=dropped)

    return kept


class StyleMixin:
    @classmethod
    def add_actions(cls, actions: dict[str, AgentAction]):
        actions["_styles"] = AgentAction(
            enabled=True,
            container=True,
            label="Styles",
            icon="mdi-palette",
            description="Style configuration",
            config={
                "art_style": AgentActionConfig(
                    type="wstemplate",
                    value="visual_styles__digital_art",
                    wstemplate_type="visual_style",
                    wstemplate_filter={"visual_type": "STYLE"},
                    label="Art Style",
                    description="The default art style to use for visual prompt generation. Can be overridden in scene settings.",
                    choices=[],
                ),
                "character_card_style": AgentActionConfig(
                    type="wstemplate",
                    value="visual_styles__character_card",
                    wstemplate_type="visual_style",
                    wstemplate_filter={"visual_type": "CHARACTER_CARD"},
                    label="Character Card",
                    description="The style to use for character card visual prompt generation",
                    choices=[],
                ),
                "character_portrait_style": AgentActionConfig(
                    type="wstemplate",
                    value="visual_styles__character_portrait",
                    wstemplate_type="visual_style",
                    wstemplate_filter={"visual_type": "CHARACTER_PORTRAIT"},
                    label="Character Portrait",
                    description="The style to use for character portrait visual prompt generation",
                    choices=[],
                ),
                "scene_card_style": AgentActionConfig(
                    type="wstemplate",
                    value="visual_styles__scene_card",
                    wstemplate_type="visual_style",
                    wstemplate_filter={"visual_type": "SCENE_CARD"},
                    label="Scene Card",
                    description="The style to use for scene card visual prompt generation",
                    choices=[],
                ),
                "scene_background_style": AgentActionConfig(
                    type="wstemplate",
                    value="visual_styles__scene_background",
                    wstemplate_type="visual_style",
                    wstemplate_filter={"visual_type": "SCENE_BACKGROUND"},
                    label="Scene Background",
                    description="The style to use for scene background visual prompt generation",
                    choices=[],
                ),
                "scene_illustration_style": AgentActionConfig(
                    type="wstemplate",
                    value="visual_styles__scene_illustration",
                    wstemplate_type="visual_style",
                    wstemplate_filter={"visual_type": "SCENE_ILLUSTRATION"},
                    label="Scene Illustration",
                    description="The style to use for scene illustration visual prompt generation",
                    choices=[],
                ),
                "object_illustration_style": AgentActionConfig(
                    type="wstemplate",
                    value="visual_styles__object_illustration",
                    wstemplate_type="visual_style",
                    wstemplate_filter={"visual_type": "OBJECT_ILLUSTRATION"},
                    label="Object Illustration",
                    description="The style to use for object illustration visual prompt generation",
                    choices=[],
                    note=AgentActionNote(
                        title="Manage styles",
                        text="Additional styles can be created in the Templates manager.",
                        icon="mdi-cube-scan",
                    ),
                ),
            },
        )
        return actions

    # helpers

    def style_template_id(self, vis_type: VIS_TYPE) -> str:
        if vis_type == VIS_TYPE.UNSPECIFIED:
            return self.resolve_config("_styles", "art_style")
        return self.resolve_config("_styles", f"{vis_type.value.lower()}_style")

    def style_template(self, vis_type: VIS_TYPE) -> VisualStyle | None:
        scene = getattr(self, "scene", None)
        if not scene:
            return None

        manager: WorldStateManager = scene.world_state_manager
        templates: Collection = manager.template_collection

        # Check for scene-level override for art style (UNSPECIFIED) only
        template_id = None
        if vis_type == VIS_TYPE.UNSPECIFIED and scene.visual_style_template:
            template_id = scene.visual_style_template
        else:
            template_id = self.style_template_id(vis_type)

        if not template_id:
            return None

        try:
            group_uid, template_uid = template_id.split("__")
        except ValueError:
            return None
        return templates.find_template(group_uid, template_uid)

    def _get_current_art_style_name(self) -> str | None:
        """Get the name of the currently active art style template"""
        if not getattr(self, "scene", None):
            return None
        template = self.style_template(VIS_TYPE.UNSPECIFIED)
        if template:
            return template.name
        return None

    def _get_current_art_style_source(self) -> str | None:
        """Get the source of the currently active art style: 'scene' or 'agent'"""
        scene = getattr(self, "scene", None)
        if not scene:
            return None
        if scene.visual_style_template:
            return "scene"
        return "agent"

    # actions

    def apply_style(
        self, prompt: VisualPrompt, template_id: str
    ) -> VisualPromptPart | None:
        template = self.style_template(template_id)
        part = None
        if template:
            part = VisualPromptPart(**template.model_dump())
            prompt.parts.insert(0, part)
        return part

    async def apply_styles(
        self, prompt: VisualPrompt, vis_type: VIS_TYPE
    ) -> VisualPrompt:
        """
        Assemble the final prompt: styles, then anchors, then whatever the LLM said.

        Async because anchor derivation may need one LLM call the first time a subject
        is seen. Every call after that is cached and does no I/O.
        """
        # Parts already present, if any. In the shipped node graph there are none: the
        # Prompt node is constructed empty and the LLM's keywords are appended to the
        # part list *after* this runs. Sanitising and budget enforcement therefore
        # cannot happen here - they run in GenerationMixin._finalize_prompt, once the
        # prompt is actually complete. Verified against a live generation, not read off
        # the graph.
        existing_parts = list(prompt.parts)

        anchor_parts = await self._insert_anchors(prompt, vis_type, existing_parts)

        template_art_style: VisualStyle | None = self.style_template(
            VIS_TYPE.UNSPECIFIED
        )
        template_subject_style: VisualStyle | None = self.style_template(vis_type)

        log.debug(
            "apply_styles",
            template_art_style=template_art_style,
            template_subject_style=template_subject_style,
        )

        if template_subject_style:
            prompt.parts.insert(
                0,
                VisualPromptPart(
                    positive_keywords_raw=template_subject_style.positive_keywords,
                    negative_keywords_raw=template_subject_style.negative_keywords,
                    positive_descriptive=template_subject_style.positive_descriptive,
                    negative_descriptive=template_subject_style.negative_descriptive,
                    instructions=template_subject_style.instructions,
                ),
            )

        if template_art_style:
            prompt.parts.insert(
                0,
                VisualPromptPart(
                    positive_keywords_raw=template_art_style.positive_keywords,
                    negative_keywords_raw=template_art_style.negative_keywords,
                    positive_descriptive=template_art_style.positive_descriptive,
                    negative_descriptive=template_art_style.negative_descriptive,
                    instructions=template_art_style.instructions,
                ),
            )

        log.debug("apply_styles.anchors", count=len(anchor_parts.ordered))

        return prompt

    def _max_prompt_tokens(self) -> int:
        """
        The image-prompt token budget.

        Deliberately not prompt_generation.max_length - that is how many tokens the LLM
        may generate while writing the prompt, which is a different number entirely.
        """
        try:
            configured = self.resolve_config("prompt_generation", "image_max_tokens")
        except Exception:
            configured = None
        return configured or DEFAULT_MAX_PROMPT_TOKENS

    async def _insert_anchors(
        self,
        prompt: VisualPrompt,
        vis_type: VIS_TYPE,
        llm_parts: list[VisualPromptPart],
    ) -> "AnchorParts":
        """
        Insert the scene anchor, then one anchor per in-frame character.

        Inserted ahead of the LLM's parts on purpose. `VisualPrompt._build_prompt`
        dedupes with `dict.fromkeys`, which keeps the first occurrence, so position is
        what makes the anchor's wording win over a vaguer duplicate from the LLM.

        Character anchors are skipped for vis types that have no cast - an object study
        should not carry the crew's appearance.
        """
        result = AnchorParts()

        scene = getattr(self, "scene", None)
        if not scene:
            return result

        scene_anchor = await self.scene_anchor(scene)
        if scene_anchor:
            result.scene = VisualPromptPart(
                positive_keywords_raw=split_anchor(scene_anchor)
            )

        if vis_type not in VIS_TYPES_WITHOUT_CAST:
            characters = list(getattr(self, "characters", None) or scene.characters)
            in_frame = characters_in_frame(prompt, characters)

            # Anchors go in for everyone in frame; _finalize_prompt keeps one and drops
            # the rest. The choice has to be made there, not here, because this runs
            # before the prompt has any parts and so has no text to judge by - picking
            # here meant picking the first character in the scene regardless of who the
            # paragraph was about.
            #
            # Only one survives: a flat prompt cannot bind attributes to separate people,
            # so "human male" and "alien woman" together produce one blended person.
            for character in in_frame:
                keywords = []

                # Before reading the anchor, not after: if the story has stated a lasting
                # change, this clears the cache so character_anchor re-derives it now
                # rather than one image later.
                await self.refresh_permanent_change(scene, character)

                character_anchor = await self.character_anchor(character)
                if character_anchor:
                    keywords.extend(split_anchor(character_anchor))

                # Wardrobe follows the identity anchor, so a scene-supplied garment can
                # still be suppressed downstream in _suppress_stale_wardrobe.
                wardrobe = await self.refresh_wardrobe(scene, character)
                if wardrobe:
                    keywords.extend(split_anchor(wardrobe))

                # RC4: a rule labelled HARD was being handed to the prompt-writing LLM
                # and silently dropped in the keyword compression. Emit it directly,
                # condensed - it is authored prose, not keywords.
                if character.visual_rules:
                    condensed = condense_visual_rule(character.visual_rules)
                    if condensed:
                        keywords.extend(split_anchor(condensed))

                if keywords:
                    result.characters.append(
                        VisualPromptPart(positive_keywords_raw=keywords)
                    )

            extra = 0 if vis_type in VIS_TYPES_SOLO else len(in_frame) - 1
            if extra > 0:
                result.characters.append(
                    VisualPromptPart(
                        positive_keywords_raw=[
                            "a second figure" if extra == 1 else f"{extra} other figures"
                        ]
                    )
                )

        ordered = result.ordered
        if not ordered:
            return result

        insert_at = prompt.parts.index(llm_parts[0]) if llm_parts else len(prompt.parts)
        for offset, part in enumerate(ordered):
            prompt.parts.insert(insert_at + offset, part)

        return result
