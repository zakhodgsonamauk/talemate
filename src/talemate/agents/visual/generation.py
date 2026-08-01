import asyncio
import json
import re
import structlog
from typing import Callable

# import talemate.agents.visual.automatic1111  # noqa: F401
# import talemate.agents.visual.comfyui  # noqa: F401
# import talemate.agents.visual.openai_image  # noqa: F401
from talemate.agents.base import (
    AgentAction,
    AgentEmission,
    set_processing,
)
import talemate.emit.async_signals as async_signals
from talemate.emit import emit
from talemate.context import active_scene
from talemate.prompts import Prompt
from .anchors import _is_wardrobe_token, _mention_count, condense_visual_rule
from .schema import (
    GEN_TYPE,
    PROMPT_TYPE,
    SEED_MODE,
    VIS_TYPE,
    GenerationResponse,
    BackendStatusType,
    GenerationRequest,
    PromptProfile,
    Resolution,
    FORMAT_TYPE,
    get_prompt_profile,
    resolve_seed,
)
from .style import (
    VIS_TYPES_WITHOUT_CAST,
    estimate_prompt_tokens,
    normalize_keyword,
    sanitise_keywords,
    strip_emphasis,
    weight_group,
)
from .exceptions import ImageEditNotAvailableError, TextToImageNotAvailableError

log = structlog.get_logger("talemate.agents.visual.generation")

# Share of the prompt budget held back for what is happening in the shot. Without a
# reserve, three derived character anchors fill the whole budget and the image ends up
# depicting nobody doing anything.
ACTION_BUDGET_RESERVE = 0.35

# Added to the negative prompt when the subject is not human. The checkpoint's prior is
# overwhelmingly human, so a non-human trait stated only positively comes back diluted.
SPECIES_NEGATIVES = ("human skin", "human ears", "ordinary skin tone")

# Booru tags for the subject's sex.
#
# The Pony-derived checkpoints are tag-trained. "human male" is natural language and
# carries little signal, while "1boy" is a tag the model actually learned. Without it the
# strongest real tags in the prompt are `solo` and `looking at viewer`, both of which pull
# toward the checkpoint's default subject - a young female - and that is what came out.
SEX_TAGS = {
    "male": ("1boy", "male focus"),
    "female": ("1girl",),
}

# The other half of the same instruction, exactly as with species. Negating the default is
# what makes the positive tag hold.
SEX_NEGATIVES = {
    "male": ("1girl", "female", "breasts", "nipples"),
    "female": ("1boy", "male focus"),
}

# Only added when the prompt says the subject is dressed. An undressed scene must not have
# its own intent negated - the scene text is the authority on what they are wearing.
#
# "nude, naked, topless" alone proved insufficient: observed live, a male subject wearing
# jeans and a tank top was rendered with the trousers open and genitals exposed. None of
# those three tags describe that, because the subject is not nude - he is dressed and
# exposed. The anatomy and state-of-undress tags are the ones that cover it.
NUDITY_NEGATIVES = (
    "nude",
    "naked",
    "topless",
    "bottomless",
    "penis",
    "exposed genitals",
    "pubic hair",
    "nipples",
    "undressing",
    "unzipped",
    "open pants",
    "uncensored",
    "nsfw",
)

# Phrases in the prompt that mean the scene intends undress. Their presence suppresses the
# nudity negatives even when clothing is also mentioned - "unbuttoning his shirt" names a
# garment while describing its removal, and negating that would fight the scene.
#
# Every entry must be unambiguous on its own. A bare "bare" was tried and had to be removed:
# it matched another character's leaked "bare chest" in a prompt whose subject was fully
# dressed, silently switching off every nudity negative. "exposed" went the same way - the
# prompt also carried "vacuum exposure". Where a stem is ambiguous, the phrase is spelled out.
_UNDRESS_INTENT_WORDS = {
    "nude",
    "naked",
    "topless",
    "bottomless",
    "shirtless",
    "undress",
    "undressing",
    "undressed",
    "unbuttoning",
    "unbuttoned",
    "unzipping",
    "unzipped",
    "stripping",
    "cleavage",
    "lingerie",
    "underwear",
    "bare chest",
    "bare torso",
    "bare shoulders",
    "bare breasts",
    "exposed skin",
}

# Pony-derived checkpoints are trained with a rating axis alongside the score tags. It is
# the intended control for this and binds harder than anatomy negatives, which were
# delivered in full and ignored. Applied only to a dressed subject - forcing a safe rating
# onto a deliberately explicit scene would fight the story.
RATING_SAFE_TAG = "rating_safe"
RATING_NEGATIVES = ("rating_explicit", "rating_questionable")

# EXPERIMENT, and the last text-side lever available.
#
# Every other one has been pulled and verified in the payload actually delivered to
# ComfyUI - booru sex tags, the full anatomy negatives, the rating axis, cfg raised to 8 -
# and the image came back explicit regardless. The score tags are what remains: they select
# for highly-rated booru images, and that corpus skews explicit, so on a checkpoint tuned
# around them they plausibly outrank `rating_safe`.
#
# The cost is real - these are what give a Pony checkpoint its polish - so they are dropped
# only for a subject the prompt says is dressed. If this does not work, the conclusion is
# that prompt-level control cannot reach this checkpoint and the model has to change (T14).
SCORE_TAGS = ("score_9", "score_8_up", "score_7_up", "score_6_up", "score_5_up")

# checkpoint filename -> prompting dialect. Order matters: first match wins.
# Unmatched checkpoints fall through to pony - the historical behavior, made
# explicit. Overridable per checkpoint via the comfyui action's
# checkpoint_profiles config (JSON: {"substring": "profile_id"}).
PROFILE_CHECKPOINT_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"pony|score_9|cyberrealistic", re.IGNORECASE), "pony"),
    (
        re.compile(r"juggernaut|realvis|realistic[ _-]?vision", re.IGNORECASE),
        "sdxl_natural",
    ),
)

_CLOTHING_WORDS = {
    "uniform",
    "shirt",
    "pants",
    "trousers",
    "jacket",
    "coat",
    "dress",
    "skirt",
    "armor",
    "armour",
    "harness",
    "belt",
    "boots",
    "gloves",
    "robe",
    "suit",
    "tunic",
    "vest",
    "jumpsuit",
    "overalls",
    "clothed",
    "clothing",
    "fully clothed",
}


def parse_distilled_response(text: str) -> tuple[str | None, str | None]:
    """
    The PROMPT and NEGATIVE lines from a distillation response.

    Tolerates markdown bolding and stray fencing around the labels, because that is
    what models actually emit, but nothing looser: a response that cannot state
    `PROMPT:` on its own line has not followed the contract and the caller falls back
    to the legacy pipeline rather than guessing.
    """
    positive = negative = None
    for line in (text or "").splitlines():
        line = line.strip().strip("*`").strip()
        upper = line.upper()
        if upper.startswith("PROMPT:") and positive is None:
            positive = line[len("PROMPT:") :].strip("*` ").strip()
        elif upper.startswith("NEGATIVE:") and negative is None:
            negative = line[len("NEGATIVE:") :].strip("*` ").strip()
    return positive or None, negative or None

# Word-boundary matching matters here: "female" contains "male" and "woman" contains
# "man", so substring tests invert the sex. \b prevents both.
_MALE_RE = re.compile(r"\b(male|man|men|boy|boys|masculine|he|him|his)\b", re.IGNORECASE)
_FEMALE_RE = re.compile(
    r"\b(female|woman|women|girl|girls|feminine|she|her|hers)\b", re.IGNORECASE
)


def normalise_sex(value: str | None) -> str | None:
    """
    Map a free-form gender string onto "male", "female", or nothing.

    `Character.gender` is free text, so it may be a bare word, a phrase, or something
    that resolves to neither. Ambiguous or absent values return None and no tags are
    added: a wrong tag actively fights the prompt, which is worse than no tag at all.
    """
    if not value or not value.strip():
        return None

    male = bool(_MALE_RE.search(value))
    female = bool(_FEMALE_RE.search(value))

    if male and not female:
        return "male"
    if female and not male:
        return "female"
    return None


def dominant_sex(text: str | None) -> str | None:
    """
    Read the sex from free prose by which signal dominates.

    Stricter matching is right for a dedicated gender field, but prose describing one
    person routinely mentions another - "her brother", "the captain and his crew" - so a
    single opposing word should not veto the answer. A clear majority decides; a tie
    decides nothing.
    """
    if not text or not text.strip():
        return None

    male = len(_MALE_RE.findall(text))
    female = len(_FEMALE_RE.findall(text))

    if male > female:
        return "male"
    if female > male:
        return "female"
    return None


# Attribute names carrying the sex outright. Characters authored from a character card use
# lowercase; ones generated in play can arrive Title-Cased, hence the case-insensitive
# lookup rather than a direct `get`.
_GENDER_ATTRIBUTE_KEYS = ("gender", "sex")

# Attributes that carry a character's visual identity beyond their derived anchor. The
# anchor is a condensed appearance line and does not mention species or equipment, so
# "Altrusian", "combat trousers", "pulse pistol" and "weapon harness" were unmatchable and
# leaked onto a human subject's prompt.
_IDENTITY_ATTRIBUTE_KEYS = (
    "species",
    "appearance",
    "gear and tech",
    "gear",
    "equipment",
    "clothing",
)

# Paraphrases the LLM reaches for in place of an anchor's own wording. Observed live:
# "purple skin" against an anchor that says "deep violet skin", which word-level matching
# could not connect. Kept deliberately narrow - a broad synonym table would start dropping
# the subject's own traits.
_WORD_SYNONYMS = {
    "violet": {"purple"},
    "purple": {"violet"},
    "crimson": {"red"},
    "red": {"crimson"},
}


def _expand_synonyms(words: set[str]) -> set[str]:
    """Add known paraphrases so a reworded trait still matches its owner."""
    expanded = set(words)
    for word in words:
        expanded |= _WORD_SYNONYMS.get(word, set())
    return expanded


def identity_words(character) -> set[str]:
    """
    Significant words describing what a character looks like and carries.

    Drawn from the identity attributes as well as the anchor, because the anchor is a
    condensed appearance line: it says nothing about species or equipment, which is how
    another character's species and gear reached a human subject's prompt.
    """
    attributes = getattr(character, "base_attributes", None) or {}
    lowered = {str(key).strip().lower(): value for key, value in attributes.items()}

    text_parts: list[str] = []
    for key in _IDENTITY_ATTRIBUTE_KEYS:
        value = lowered.get(key)
        if isinstance(value, str) and value.strip():
            text_parts.append(value)

    words = {
        word
        for part in text_parts
        for word in re.findall(r"[\w'-]+", part.lower())
        if len(word) > 3
    }
    return _expand_synonyms(words)

# Fallback prose. A character generated during play may have no gender attribute at all -
# observed with a character whose attributes were Age/Appearance/Background/Personality and
# nothing else - while the appearance text says "his beard is full".
_SEX_PROSE_ATTRIBUTE_KEYS = ("appearance", "description", "physical description")


def character_sex(character) -> str | None:
    """
    Determine a character's sex from the best evidence available.

    Explicit gender attribute first and read strictly. Only if that is missing or
    unreadable does prose get consulted, and then by dominant signal.
    """
    if character is None:
        return None

    attributes = getattr(character, "base_attributes", None) or {}
    lowered = {str(key).strip().lower(): value for key, value in attributes.items()}

    stated = False
    for key in _GENDER_ATTRIBUTE_KEYS:
        value = str(lowered.get(key) or "").strip()
        if not value:
            continue
        stated = True
        sex = normalise_sex(value)
        if sex:
            return sex

    # A gender that was stated but reads as neither - "non-binary", "androgynous" - is an
    # answer, not a gap. Prose full of gendered words must not overrule it; only a missing
    # attribute licenses the fallback.
    if stated:
        return None

    for key in _SEX_PROSE_ATTRIBUTE_KEYS:
        sex = dominant_sex(str(lowered.get(key) or ""))
        if sex:
            return sex

    return dominant_sex(str(getattr(character, "description", "") or ""))

# Cost of one emphasis group: the brackets and the weight itself.
EMPHASIS_TOKEN_OVERHEAD = 4

# Words marking a keyword as about a person or what they are doing, rather than about the
# room. Setting-duplication filtering must never touch these.
_BODY_AND_POSE_WORDS = {
    "hand",
    "hands",
    "arm",
    "arms",
    "head",
    "face",
    "eye",
    "eyes",
    "mouth",
    "jaw",
    "shoulder",
    "shoulders",
    "back",
    "chest",
    "knee",
    "knees",
    "foot",
    "feet",
    "finger",
    "fingers",
    "skin",
    "hair",
    "figure",
    "silhouette",
    "profile",
    "expression",
    "glance",
    "gaze",
    "posture",
    "stance",
}


def _describes_action(keyword: str) -> bool:
    """
    Whether a keyword is about a person or a moment rather than a place.

    A gerund is the strongest signal - "leaning", "gripping", "watching" - with body and
    pose words as the second. Cheap and deliberately generous: wrongly keeping a keyword
    costs a token, wrongly dropping one costs the subject of the image.
    """
    words = re.findall(r"[\w'-]+", keyword.lower())
    return any(
        word.endswith("ing") and len(word) > 5 or word in _BODY_AND_POSE_WORDS
        for word in words
    )

async_signals.register(
    "agent.visual.generation.before_generate",
    "agent.visual.generation.after_generate",
)


class GenerationEmission(AgentEmission):
    request: GenerationRequest
    response: GenerationResponse


class GenerationMixin:
    # helpers

    @property
    def can_edit_images(self) -> bool:
        return (
            self.backend_image_edit is not None
            and self.backend_image_edit.status.type == BackendStatusType.OK
        )

    @property
    def can_generate_images(self) -> bool:
        return (
            self.backend is not None
            and self.backend.status.type == BackendStatusType.OK
        )

    def resolution(self, format: FORMAT_TYPE, action: AgentAction) -> Resolution:
        if format == FORMAT_TYPE.LANDSCAPE:
            w, h = action.config["resolution_landscape"].value
            return Resolution(width=w, height=h)
        elif format == FORMAT_TYPE.PORTRAIT:
            w, h = action.config["resolution_portrait"].value
            return Resolution(width=w, height=h)
        w, h = action.config["resolution_square"].value
        return Resolution(width=w, height=h)

    async def _auto_save_generated_asset(
        self, request: GenerationRequest, response: GenerationResponse
    ) -> None:
        """
        Auto-save generated images as scene assets when requested via AssetAttachmentContext.
        """
        try:
            ctx = request.asset_attachment_context
            should_save = bool(ctx and ctx.save_asset)
        except Exception as e:
            # If anything goes wrong inspecting ctx, fail open (no auto-save).
            should_save = False
            log.error("auto_save_generated_asset.failed", error=str(e), request=request)

        if not should_save or not response.image_data:
            return

        scene = active_scene.get()
        if not scene:
            return

        try:
            await scene.assets.add_asset_from_generation_response(response)
            response.saved = True
        except Exception as e:
            log.error(
                "auto_save_generated_image.failed",
                error=str(e),
                request_id=request.id,
                vis_type=str(request.vis_type),
                character_name=request.character_name,
            )

    # errors

    async def on_image_generation_error(self, error: Exception):
        emit(
            "image_generation_failed",
            websocket_passthrough=True,
            data={"error": str(error)},
        )
        emit("status", "Image generation failed.", status="error")

    def _track_generation_task(self, task: asyncio.Task, backend: object):
        """
        Track a generation task and its backend for cancellation support.
        Automatically removes the task from tracking when it completes.
        """
        # Initialize tracking structures if needed
        if not hasattr(self, "_active_generation_tasks"):
            self._active_generation_tasks: set[asyncio.Task] = set()
        if not hasattr(self, "_generation_task_backends"):
            self._generation_task_backends: dict[asyncio.Task, object] = {}

        # Add task to tracking
        self._active_generation_tasks.add(task)
        self._generation_task_backends[task] = backend

        # Remove task from tracking when it completes
        def remove_task(fut):
            self._active_generation_tasks.discard(task)
            self._generation_task_backends.pop(task, None)

        task.add_done_callback(remove_task)

    def _apply_seed(self, request: GenerationRequest) -> None:
        """
        Fill in the seed from configuration, unless the caller already set one.

        A pinned seed holds palette and rendering style steady across a scene's images.
        It does not hold character identity steady - visual anchors do that. Default mode
        is RANDOM, so this is a no-op until someone opts in.
        """
        if request.sampler_settings.seed is not None:
            return

        try:
            mode = self.resolve_config("_config", "seed_mode") or SEED_MODE.RANDOM
            fixed_seed = self.resolve_config("_config", "seed")
        except Exception as e:
            log.debug("apply_seed.config_unavailable", error=str(e))
            return

        seed = resolve_seed(mode, scene=active_scene.get(), fixed_seed=fixed_seed)
        if seed is not None:
            request.sampler_settings.seed = seed
            log.debug("apply_seed", mode=str(mode), seed=seed)

    def resolve_prompt_profile(
        self,
        request: "GenerationRequest | None" = None,
        checkpoint: str | None = None,
    ) -> PromptProfile:
        """
        The prompting dialect for the checkpoint this request will render on.

        Precedence mirrors comfyui.resolve_checkpoint: explicit arg > request
        extra_config override > agent-configured model. DESCRIPTIVE backends
        resolve to the prose profile regardless of checkpoint. Unknown
        checkpoints resolve to pony - the historical behavior, made explicit.
        """
        if request is not None and request.prompt_profile:
            return get_prompt_profile(request.prompt_profile)

        backend = (
            self.backend_image_edit
            if request is not None and request.gen_type == GEN_TYPE.IMAGE_EDIT
            else getattr(self, "backend", None)
        )
        if (
            getattr(backend, "prompt_type", PROMPT_TYPE.KEYWORDS)
            != PROMPT_TYPE.KEYWORDS
        ):
            return get_prompt_profile("descriptive")

        ckpt = checkpoint
        if not ckpt and request is not None:
            value = request.extra_config.get("checkpoint")
            ckpt = value if isinstance(value, str) and value else None
        if not ckpt:
            for action_name in ("comfyui_image_create", "comfyui_image_edit"):
                try:
                    ckpt = self.resolve_config(action_name, "model")
                except Exception:
                    ckpt = None
                if ckpt:
                    break

        if not ckpt:
            return get_prompt_profile("pony")

        # per-checkpoint override: JSON {"filename substring": "profile id"}
        overrides: dict = {}
        for action_name in ("comfyui_image_create", "comfyui_image_edit"):
            try:
                raw = self.resolve_config(action_name, "checkpoint_profiles")
            except Exception:
                continue
            if raw:
                try:
                    overrides = json.loads(raw) if isinstance(raw, str) else dict(raw)
                except Exception:
                    log.warning(
                        "prompt_profile.override_parse_failed", raw=str(raw)[:100]
                    )
                break
        for needle, profile_id in overrides.items():
            if needle and needle.lower() in ckpt.lower():
                return get_prompt_profile(profile_id)

        for pattern, profile_id in PROFILE_CHECKPOINT_PATTERNS:
            if pattern.search(ckpt):
                return get_prompt_profile(profile_id)

        return get_prompt_profile("pony")

    async def _finalize_prompt(self, request: GenerationRequest) -> None:
        """
        Last pass over the assembled positive prompt, before it goes to a backend.

        This runs here rather than in apply_styles for a concrete reason: the node graph
        constructs the VisualPrompt empty, lets apply_styles add styles and anchors, and
        only then appends the LLM's keywords. apply_styles never sees the LLM's output,
        so a filter placed there silently does nothing. This is the first point where the
        prompt is complete.

        Three jobs, in order:

        1. Drop keywords a diffusion model cannot render.
        2. Drop anchors for characters who are not in the shot.
        3. Trim to the token budget.
        """
        # The pending distillation carries a complete prompt of its own and the
        # graph's local keyword write can come up EMPTY on the prompt-only
        # (Adjust & Visualize) path - so consume it before the empty-prompt
        # bail below, or the finished prompt is silently thrown away and the
        # preview modal shows nothing (observed live, flow visual:visualize:652e00).
        if (
            getattr(self, "distillation_enabled", False)
            and not request.distilled
            and await self._consume_pending_distillation(request)
        ):
            request.distilled = True

        if not request.prompt:
            return

        # Only comma-delimited keyword prompts. A DESCRIPTIVE backend receives prose,
        # where splitting on commas would shred sentences.
        backend = (
            self.backend_image_edit
            if request.gen_type == GEN_TYPE.IMAGE_EDIT
            else self.backend
        )
        if getattr(backend, "prompt_type", PROMPT_TYPE.KEYWORDS) != PROMPT_TYPE.KEYWORDS:
            return

        # A distilled prompt is final. This runs twice per image - once from the
        # FinalizePrompt preview node, once from generate - and the second pass must
        # neither pay a second LLM call nor shred the finished prompt through the
        # legacy keyword surgery below.
        if request.distilled:
            return

        if getattr(self, "distillation_enabled", False):
            # The pending task was already consumed (or absent) above; distill
            # fresh from the scene facts.
            if await self._distill_prompt(request):
                request.distilled = True
                return
            # Distillation declined or failed - the legacy pipeline is the fallback.

        original = request.prompt
        # Regenerate re-submits a previous request, so the prompt may already be
        # weighted. Strip first or emphasis compounds on every pass.
        keywords = [
            normalize_keyword(kw)
            for kw in strip_emphasis(original).split(",")
            if kw.strip()
        ]

        profile = self.resolve_prompt_profile(request)
        request.prompt_profile = profile.id

        keywords = sanitise_keywords(keywords)
        keywords = await self._drop_absent_character_anchors(keywords, request)
        keywords = await self._suppress_stale_wardrobe(keywords, request)
        keywords = await self._drop_duplicate_setting(keywords, request)
        keywords = await self._drop_secondary_traits(keywords, request)
        if profile.id == "sdxl_natural":
            # Pony conventions are noise to this checkpoint family: strip any
            # score/rating/source tags the style templates injected, and skip
            # the tag adders below - the sex/rating axes do not exist here.
            keywords = [
                kw
                for kw in keywords
                if not re.match(r"\s*(score_|rating_|source_)", kw, re.IGNORECASE)
            ]
        else:
            # Before the budget trim so the tags are accounted for rather than
            # pushed out.
            keywords = await self._add_sex_tags(keywords, request)
            keywords = await self._add_rating_tags(keywords, request)
            keywords = await self._drop_score_tags_when_dressed(keywords, request)
        keywords = await self._trim_to_budget(keywords, request)

        keywords = list(dict.fromkeys(keywords))
        await self._add_species_negatives(keywords, request)
        await self._add_sex_negatives(keywords, request)
        await self._add_rating_negatives(keywords, request)
        request.prompt = await self._render_with_emphasis(keywords, request)

        if request.prompt != original:
            log.debug(
                "finalize_prompt",
                before=estimate_prompt_tokens(original),
                after=estimate_prompt_tokens(request.prompt),
            )

    async def begin_prompt_distillation(
        self,
        vis_type: str | None = None,
        instructions: str | None = None,
        character_name: str | None = None,
    ) -> str:
        """
        Start the distillation call in parallel with the local keyword write.

        Called from the generate-image template via the `agent_action` template
        global. The template renders before the graph's local LLM call, and the
        two calls have no data dependency - distillation reads the fact pack, not
        the local keywords - so running them sequentially was pure wall-clock
        waste. nest_asyncio means the render runs nested on the main loop, so the
        task created here outlives the render.

        Skipped when there is neither an instructions paragraph nor an explicit
        character: without those, subject choice at this point would fall through
        to scene order, and the late path can at least read the local keywords.

        Returns "" so the template call renders as nothing.
        """
        if not getattr(self, "distillation_enabled", False):
            return ""

        instructions = (instructions or "").strip()
        character_name = (character_name or "").strip()
        if not instructions and not character_name:
            log.debug("distill_prompt.prestart_declined", reason="no subject evidence")
            return ""

        try:
            vt = VIS_TYPE(vis_type)
        except (ValueError, TypeError):
            log.debug("distill_prompt.prestart_declined", reason="bad vis_type", vis_type=vis_type)
            return ""
        if vt in VIS_TYPES_WITHOUT_CAST:
            log.debug("distill_prompt.prestart_declined", reason="castless vis_type", vis_type=str(vt))
            return ""

        request = GenerationRequest(
            prompt="",
            vis_type=vt,
            instructions=instructions or None,
            character_name=character_name or None,
        )
        # The dialect is part of the ask's identity: a checkpoint switch between
        # compose and finalize must not serve a stale-dialect prompt.
        profile = self.resolve_prompt_profile(request)
        request.prompt_profile = profile.id
        key = (vt, character_name, instructions, profile.id)

        # Replace, and cancel, any pending task a previous compose abandoned.
        stale = getattr(self, "_pending_distillation", None)
        if stale:
            stale[2].cancel()

        task = asyncio.create_task(self._distill_prompt(request))
        self._pending_distillation = (key, request, task)
        log.debug("distill_prompt.prestarted", vis_type=str(vt), subject=character_name)
        return ""

    async def _consume_pending_distillation(self, request: GenerationRequest) -> bool:
        """
        Use the distillation that begin_prompt_distillation started, if it matches.

        Matching is on (vis_type, character_name, instructions) - the identity of
        the ask. A mismatch means the pending task belongs to some other request
        (or the ask changed between compose and finalize); it is cancelled and the
        caller distills fresh rather than serving the wrong prompt.
        """
        pending = getattr(self, "_pending_distillation", None)
        if not pending:
            return False
        self._pending_distillation = None

        key, pre_request, task = pending
        expected = (
            request.vis_type,
            (request.character_name or "").strip(),
            (request.instructions or "").strip(),
            self.resolve_prompt_profile(request).id,
        )
        if key != expected:
            task.cancel()
            # Both keys in full: a mismatch here silently costs the parallelism,
            # so the log has to show exactly which component disagreed.
            log.warning(
                "distill_prompt.pending_mismatch",
                pending={
                    "vis_type": str(key[0]),
                    "character": key[1],
                    "instructions": key[2][:80],
                    "profile": key[3] if len(key) > 3 else None,
                },
                expected={
                    "vis_type": str(expected[0]),
                    "character": expected[1],
                    "instructions": expected[2][:80],
                    "profile": expected[3],
                },
            )
            return False

        try:
            ok = await task
        except asyncio.CancelledError:
            return False
        except Exception as e:
            log.warning("distill_prompt.pending_failed", error=str(e))
            return False

        if not ok:
            return False

        request.prompt = pre_request.prompt
        request.negative_prompt = pre_request.negative_prompt
        request.prompt_profile = pre_request.prompt_profile
        log.debug("distill_prompt.pending_consumed", subject=key[1] or None)
        return True

    @set_processing
    async def _distill_prompt(self, request: GenerationRequest) -> bool:
        """
        Write the finished prompt in one LLM call from the scene facts.

        The alternative to the legacy pipeline below, whose keyword lists and boolean
        gates reason over a prompt describing everyone present and have failed in both
        directions repeatedly. Here the facts go in structured - subject identity,
        wardrobe, rules, setting, the moment - and the model returns the final
        positive and negative prompts. Validated across Ollama cloud models by
        scripts/visual_model_bakeoff.py; see docs/fork/visual-distillation-design.md.

        Returns False without touching the request when anything is missing or the
        response breaks the two-line contract, so the caller can fall back. An image
        with a legacy prompt beats no image.
        """
        scene = active_scene.get()
        if not scene or request.vis_type in VIS_TYPES_WITHOUT_CAST:
            return False

        characters = list(getattr(self, "characters", None) or scene.characters)
        keywords = [
            kw for kw in strip_emphasis(request.prompt or "").split(",") if kw.strip()
        ]
        subject = self._choose_subject(characters, keywords, request)
        if not subject:
            return False

        identity = await self.character_anchor(subject)
        wardrobe = await self.refresh_wardrobe(scene, subject)
        rules = condense_visual_rule(subject.visual_rules)
        sex = character_sex(subject)
        setting = await self.scene_anchor(scene)
        location = getattr(getattr(scene, "world_state", None), "location", None)
        others = ", ".join(
            f"{c.name} ({character_sex(c) or 'sex unknown'})"
            for c in characters
            if c is not subject
        )

        # Computed here rather than in the template: context_history routes through
        # the summarizer agent, and a missing or broken summarizer must degrade to a
        # recap-less distillation, not a failed image.
        try:
            recent = list(scene.context_history(budget=600))
        except Exception as e:
            log.warning("distill_prompt.no_context_history", error=str(e))
            recent = []

        # Distillation may run on its own client: the capable cloud model does this
        # one call, while the agent's other prompt work (whose output distillation
        # discards) stays on something fast and local.
        client = self.client
        try:
            configured = (self.resolve_config("_distillation", "client") or "").strip()
        except Exception:
            configured = ""
        if configured:
            from talemate.instance import get_client

            try:
                client = get_client(configured)
            except KeyError:
                log.warning(
                    "distill_prompt.client_not_found",
                    configured=configured,
                    fallback=getattr(self.client, "name", None),
                )

        profile = self.resolve_prompt_profile(request)
        request.prompt_profile = profile.id

        prompt_vars = {
            "scene": scene,
            "recent": recent,
            "subject": subject,
            "identity": identity or "",
            "wardrobe": wardrobe or "",
            "rules": rules or "",
            "sex": sex or "",
            "others": others,
            "scene_anchor": setting or "",
            "location": location or "",
            "instructions": (request.instructions or "").strip(),
            "max_prompt_tokens": min(self._max_prompt_tokens(), profile.max_prompt_tokens),
            "dialect": profile.dialect_instructions,
            "profile_id": profile.id,
        }

        # Two attempts: cloud models refuse borderline content flakily rather than
        # consistently - observed live, the same prompt refused once and answered
        # cleanly on the next call. A refusal parses as no PROMPT: line, so one
        # retry converts most of them; a second failure falls through to legacy.
        positive = negative = None
        for attempt in range(2):
            try:
                raw, _ = await Prompt.request(
                    "visual.distill-image-prompt",
                    client,
                    # Parametric kind: "visualize" alone caps the response at 150
                    # tokens, which a thinking model spends before writing a
                    # single keyword.
                    "visualize_long",
                    vars=dict(prompt_vars),
                )
            except Exception as e:
                log.warning(
                    "distill_prompt.failed",
                    subject=subject.name,
                    attempt=attempt + 1,
                    error=str(e),
                )
                continue

            positive, negative = parse_distilled_response(raw)
            if positive:
                break
            log.warning(
                "distill_prompt.unparseable",
                subject=subject.name,
                attempt=attempt + 1,
                response=(raw or "")[:200],
            )

        if not positive:
            return False

        # The style templates were already flattened into the incoming prompt string,
        # where they can no longer be told apart from the LLM keywords - so they are
        # re-fetched from their source and re-applied around the distilled core,
        # in the dialect the target checkpoint reads.
        style_positive: list[str] = []
        style_negative: list[str] = []
        for vis_type in (VIS_TYPE.UNSPECIFIED, request.vis_type):
            style = self.style_template(vis_type)
            if style:
                style_positive.extend(style.positive_keywords or [])
                style_negative.extend(style.negative_keywords or [])

        quality_keywords = [
            kw.strip() for kw in profile.quality_prefix.split(",") if kw.strip()
        ]

        if profile.style_render == "natural":
            # Natural-language checkpoints (Juggernaut family): no score/rating/
            # source tags anywhere - strip them from template data too, since the
            # user's installed style templates are Pony-era. The surviving style
            # keywords (medium, render quality) join as a trailing style phrase.
            def _tagless(keywords: list[str]) -> list[str]:
                return [
                    kw
                    for kw in keywords
                    if not re.match(
                        r"\s*(score_|rating_|source_|booru)", kw, re.IGNORECASE
                    )
                ]

            style_phrase = ", ".join(
                dict.fromkeys(_tagless([*quality_keywords, *style_positive]))
            )
            body = positive.strip().rstrip(",")

            # Deterministic safety net: the LLM overruns the token cap even
            # when told twice (observed live: ~120 CLIP tokens against a 75
            # cap). Sentences are ordered most-important-first by contract,
            # so trim whole sentences from the tail until the budget holds.
            budget = profile.max_prompt_tokens - estimate_prompt_tokens(style_phrase)
            if estimate_prompt_tokens(body) > budget:
                sentences = re.split(r"(?<=[.!?])\s+", body)
                kept: list[str] = []
                for sentence in sentences:
                    candidate = " ".join([*kept, sentence])
                    # the first sentence is kept even when it alone exceeds
                    # the budget - a slightly over-budget prompt that depicts
                    # the subject beats an empty one
                    if kept and estimate_prompt_tokens(candidate) > budget:
                        break
                    kept.append(sentence)
                trimmed = " ".join(kept)
                log.debug(
                    "distill_prompt.sentence_trim",
                    before=estimate_prompt_tokens(body),
                    after=estimate_prompt_tokens(trimmed),
                    budget=budget,
                    dropped_sentences=len(sentences) - len(kept),
                )
                body = trimmed

            if style_phrase:
                if body and body[-1] not in ".!?":
                    body += "."
                request.prompt = f"{body} {style_phrase}"
            else:
                request.prompt = body
            negative_keywords = _tagless(
                [
                    kw.strip()
                    for kw in [
                        *(negative or "").split(","),
                        *profile.negative_base.split(","),
                        *style_negative,
                    ]
                    if kw.strip()
                ]
            )
            request.negative_prompt = ", ".join(dict.fromkeys(negative_keywords))
        else:
            # Tag-rendered checkpoints (Pony family): quality prefix first (the
            # profile's full score chain - dedupe absorbs whatever subset the
            # style template already carries), then style tags, then the
            # distilled core.
            positive_keywords = [
                normalize_keyword(kw)
                for kw in [*quality_keywords, *style_positive, *positive.split(",")]
                if kw.strip()
            ]
            negative_keywords = [
                kw.strip()
                for kw in [
                    *(negative or "").split(","),
                    *profile.negative_base.split(","),
                    *style_negative,
                ]
                if kw.strip()
            ]
            request.prompt = ", ".join(dict.fromkeys(positive_keywords))
            request.negative_prompt = ", ".join(dict.fromkeys(negative_keywords))

        log.debug(
            "distill_prompt",
            subject=subject.name,
            profile=profile.id,
            tokens=estimate_prompt_tokens(request.prompt),
            prompt=request.prompt,
            negative=request.negative_prompt,
        )
        return True

    async def _drop_absent_character_anchors(
        self, keywords: list[str], request: GenerationRequest
    ) -> list[str]:
        """
        Remove a character's appearance keywords when nothing in the prompt names them.

        The anchors were inserted before the LLM's keywords existed, so every active
        character got one. The LLM's own keywords name whoever is actually in the shot -
        that is the evidence used here. Anchors never contain proper names (the
        derivation template forbids it), so searching the whole keyword list for a name
        cannot match the anchor itself.
        """
        scene = active_scene.get()
        if not scene or request.vis_type in VIS_TYPES_WITHOUT_CAST:
            return keywords

        # Same source as _insert_anchors, so what gets pruned matches what was added.
        characters = list(getattr(self, "characters", None) or scene.characters)
        if len(characters) < 2:
            # Nothing to disambiguate; a single-cast scene keeps its anchor.
            return keywords

        haystack = ", ".join(keywords)

        # If the prompt names nobody at all, there is no evidence to act on. Pruning here
        # would strip every anchor and leave an image with no subject - which is what
        # happened when the LLM returned keywords mentioning no names.
        if not any(_mention_count(haystack, c.name) for c in characters):
            log.debug("drop_absent_character_anchors.no_names_in_prompt")
            return keywords

        surviving = list(keywords)

        for character in characters:
            if _mention_count(haystack, character.name):
                continue

            anchor = await self.character_anchor(character)
            drop = set()
            if anchor:
                drop.update(token.strip().lower() for token in anchor.split(","))
            if character.visual_rules:
                drop.update(
                    token.strip().lower() for token in character.visual_rules.split(",")
                )

            if not drop:
                continue

            before = len(surviving)
            surviving = [kw for kw in surviving if kw.strip().lower() not in drop]
            if len(surviving) != before:
                log.debug(
                    "finalize_prompt.dropped_absent_character",
                    character=character.name,
                    removed=before - len(surviving),
                )

        return surviving

    def _choose_subject(self, characters: list, keywords: list[str], request):
        """
        Decide who the image is of.

        Evidence, strongest first:

        1. an explicit `character_name` from the caller - that is a statement, not a hint
        2. the text being visualised (`request.instructions`) - the paragraph the user
           clicked on, which is what they expect to drive it
        3. names in the LLM's keywords
        4. scene order, as a last resort

        Anchor insertion cannot make this call: `apply_styles` runs before the prompt has
        any parts, so it has no text to read and was falling straight through to scene
        order. The result was that the first character in the scene got drawn no matter
        what the paragraph said.
        """
        if not characters:
            return None

        if request.character_name:
            for character in characters:
                if character.name.lower() == request.character_name.strip().lower():
                    log.debug("choose_subject", by="character_name", name=character.name)
                    return character

        for source, evidence in (
            ("instructions", request.instructions or ""),
            ("keywords", ", ".join(keywords)),
        ):
            if not evidence.strip():
                continue
            scored = [(_mention_count(evidence, c.name), c) for c in characters]
            scored = [row for row in scored if row[0]]
            if not scored:
                continue
            # Stable sort keeps scene order for ties.
            scored.sort(key=lambda row: row[0], reverse=True)
            log.debug(
                "choose_subject",
                by=source,
                name=scored[0][1].name,
                counts={c.name: n for n, c in scored},
            )
            return scored[0][1]

        log.debug("choose_subject", by="scene order", name=characters[0].name)
        return characters[0]

    async def _primary_and_secondary(
        self, keywords: list[str], request: GenerationRequest
    ) -> tuple[set[str], set[str]]:
        """
        Split character anchor tokens into the subject being drawn and everyone else.

        Only the primary subject's anchor was inserted, so whichever character has the
        most of its anchor present in the prompt is the one being drawn. Deriving it from
        the prompt rather than recomputing keeps this consistent with what was actually
        inserted, even if the in-frame decision would come out differently now.
        """
        scene = active_scene.get()
        if not scene or request.vis_type in VIS_TYPES_WITHOUT_CAST:
            return set(), set()

        characters = list(getattr(self, "characters", None) or scene.characters)
        present = {kw.strip().lower() for kw in keywords}

        anchors: list[tuple[object, set[str]]] = []
        for character in characters:
            tokens: set[str] = set()

            anchor = await self.character_anchor(character)
            if anchor:
                tokens.update(t.strip().lower() for t in anchor.split(","))

            # Rules belong to the character as much as the anchor does. Leaving them out
            # meant Elmer's "head / face in shadow" survived onto a portrait of Kaira.
            if character.visual_rules:
                condensed = condense_visual_rule(character.visual_rules)
                if condensed:
                    tokens.update(t.strip().lower() for t in condensed.split(","))

            if tokens:
                anchors.append((character, tokens))

        if not anchors:
            return set(), set()

        primary = self._choose_subject(
            [character for character, _ in anchors], keywords, request
        )
        if not primary:
            return set(), set()

        primary_tokens = next(
            tokens for character, tokens in anchors if character is primary
        )
        if not primary_tokens & present:
            # The chosen subject's anchor is not in the prompt, so there is nothing here
            # to emphasise and nothing of theirs to protect.
            return set(), set()

        secondary: set[str] = set()
        for character, tokens in anchors:
            if character is not primary:
                secondary |= tokens
        # A trait shared by both characters belongs to the one being drawn.
        secondary -= primary_tokens

        self._primary_character = primary
        return primary_tokens, secondary

    async def _drop_secondary_traits(
        self, keywords: list[str], request: GenerationRequest
    ) -> list[str]:
        """
        Remove other characters' identity traits written by the LLM.

        The single-subject rule governs the anchors we insert, but the LLM describes
        everyone present. Observed live: Elmer as the subject, with "violet skin" - a
        trait belonging to Kaira - sitting in the same prompt. To a diffusion model that
        is not a second character, it is a contradictory adjective on the first.
        """
        scene = active_scene.get()
        if not scene:
            return keywords

        primary, secondary = await self._primary_and_secondary(keywords, request)
        if not secondary:
            return keywords

        def words_of(tokens: set[str]) -> set[str]:
            return {
                word
                for token in tokens
                for word in re.findall(r"[\w'-]+", token.lower())
                if len(word) > 3
            }

        # Word-level, not exact-token. The LLM writes "violet skin" where the anchor says
        # "deep violet skin with geometric facial markings" - an exact match would have
        # let the live case straight through.
        #
        # Widened beyond the anchors with each character's identity attributes and known
        # paraphrases. The anchor is a condensed appearance line: it names no species and no
        # equipment, so "Altrusian", "combat trousers", "pulse pistol" and "weapon harness"
        # were unmatchable, and "purple skin" could not be connected to an anchor saying
        # "violet". All of those reached a lone human male's prompt in one generation.
        scene_characters = list(getattr(self, "characters", None) or scene.characters)
        subject = getattr(self, "_primary_character", None)

        secondary_words = words_of(secondary)
        primary_words = _expand_synonyms(words_of(primary))
        for character in scene_characters:
            if subject is not None and character is subject:
                primary_words |= identity_words(character)
            else:
                secondary_words |= identity_words(character)

        # `secondary_words` deliberately keeps words the subject shares - "skin" belongs to
        # both - because the test below requires every word of a keyword to be attributable
        # to the other character. Removing shared words would break "purple skin", where
        # only "purple" is exclusively theirs. `exclusive` carries that distinction instead.
        exclusive = secondary_words - primary_words
        if not exclusive:
            return keywords

        kept, dropped = [], []
        for keyword in keywords:
            lowered = keyword.strip().lower()
            if lowered in primary:
                kept.append(keyword)
                continue
            words = {w for w in re.findall(r"[\w'-]+", lowered) if len(w) > 3}
            # Every significant word belongs to the other character, and at least one is
            # theirs alone.
            if words and words <= secondary_words and words & exclusive:
                dropped.append(keyword)
            else:
                kept.append(keyword)

        if dropped:
            log.debug("drop_secondary_traits", dropped=dropped)
        return kept

    async def _render_with_emphasis(
        self, keywords: list[str], request: GenerationRequest
    ) -> str:
        """
        Join the keywords, weighting the identity group.

        Runs last, after every filter. Emphasis brackets would break the string
        comparisons that dedupe, suppression and budgeting all rely on, so nothing
        upstream may see them.
        """
        weight = self._identity_weight()
        if abs(weight - 1.0) < 0.01:
            return ", ".join(keywords)

        # Primary only. Weighting every character's vocabulary emphasises a trait
        # belonging to someone who is not being drawn.
        identity, _secondary = await self._primary_and_secondary(keywords, request)
        if not identity:
            return ", ".join(keywords)

        rendered: list[str] = []
        group: list[str] = []

        def flush():
            if group:
                rendered.append(weight_group(group, weight))
                group.clear()

        for keyword in keywords:
            if keyword.strip().lower() in identity:
                group.append(keyword)
            else:
                flush()
                rendered.append(keyword)
        flush()

        return ", ".join(rendered)

    def _identity_weight(self) -> float:
        try:
            configured = self.resolve_config("prompt_generation", "identity_weight")
        except Exception:
            configured = None
        return float(configured) if configured else 1.0

    async def _subject_sex(
        self, keywords: list[str], request: GenerationRequest
    ) -> str | None:
        """The sex of the character actually being drawn, or None if unclear."""
        scene = active_scene.get()
        if not scene or request.vis_type in VIS_TYPES_WITHOUT_CAST:
            return None

        # Chosen directly rather than through `_primary_and_secondary`, which only records
        # the subject once it has confirmed the anchor's own wording is present in the
        # prompt. That guard is right for deciding whose traits to protect, but wrong here:
        # the LLM paraphrases anchors - "purple skin" where the anchor says "violet skin" -
        # and sex conditioning would silently do nothing every time it did.
        characters = list(getattr(self, "characters", None) or scene.characters)
        primary = self._choose_subject(characters, keywords, request)
        if not primary:
            return None

        return character_sex(primary)

    def _subject_is_dressed(self, keywords: list[str]) -> bool:
        """
        Whether the prompt describes a clothed subject, by weight of evidence.

        Interim, and known to be the wrong instrument - it reasons over a prompt that
        describes everyone present, when the question concerns one person. T16 replaces it
        with the per-character `visual_wardrobe` reinforcement. Kept behind one function so
        that replacement is a single edit.

        A veto was tried and failed: one leaked "bare chest" from another character switched
        off every nudity negative for a subject wearing four garments. Counting instead lets
        a deliberate undress beat still win - "unbuttoning her shirt" is one garment against
        one undress phrase - while a stray word cannot disarm a dressed subject. A tie goes
        to undress, so the scene keeps the benefit of the doubt.

        Each keyword is classified once, undress before clothing, because a phrase naming a
        garment while describing its removal would otherwise vote on both sides.
        """
        garments = 0
        undress = 0
        for keyword in (k.lower() for k in keywords):
            if any(word in keyword for word in _UNDRESS_INTENT_WORDS):
                undress += 1
            elif any(word in keyword for word in _CLOTHING_WORDS):
                garments += 1

        return garments > undress

    async def _drop_score_tags_when_dressed(
        self, keywords: list[str], request: GenerationRequest
    ) -> list[str]:
        """
        Remove the Pony score tags from a clothed subject's prompt.

        See SCORE_TAGS: they bias toward explicit output on this checkpoint family, and by
        this point they are the only text-side lever left untried. Scoped to dressed
        subjects so an explicit scene keeps the quality tags it benefits from.
        """
        scene = active_scene.get()
        if not scene or request.vis_type in VIS_TYPES_WITHOUT_CAST:
            return keywords

        if not self._subject_is_dressed(keywords):
            return keywords

        kept = [kw for kw in keywords if kw.strip().lower() not in SCORE_TAGS]
        if len(kept) != len(keywords):
            log.debug(
                "drop_score_tags",
                dropped=len(keywords) - len(kept),
                reason="dressed subject on a checkpoint whose score tags skew explicit",
            )
        return kept

    async def _add_rating_tags(
        self, keywords: list[str], request: GenerationRequest
    ) -> list[str]:
        """
        Ask a Pony-derived checkpoint for a safe rating when the subject is dressed.

        The score tags are part of the problem they solve. `score_9, score_8_up,
        score_7_up` select for highly-rated booru images, and on that corpus highly-rated
        skews explicit - so the positive prompt carries a bias the anatomy negatives then
        have to fight. Observed live: a fully dressed subject, the complete set of nudity
        negatives delivered to ComfyUI, and an explicit image anyway.

        Rating is a top-level axis of the training data rather than a description of body
        parts, which is why it binds harder than the negatives did.

        Deliberately independent of sex resolution: rating has nothing to do with sex, and
        sex resolution has already proven fragile enough that coupling them would risk
        losing this too.
        """
        scene = active_scene.get()
        if not scene or request.vis_type in VIS_TYPES_WITHOUT_CAST:
            return keywords

        if not self._subject_is_dressed(keywords):
            return keywords

        if RATING_SAFE_TAG in keywords:
            return keywords

        log.debug("rating_tags.added", added=[RATING_SAFE_TAG])
        return [RATING_SAFE_TAG, *keywords]

    async def _add_rating_negatives(
        self, keywords: list[str], request: GenerationRequest
    ) -> None:
        """Negate the explicit end of the rating axis for a dressed subject."""
        scene = active_scene.get()
        if not scene or request.vis_type in VIS_TYPES_WITHOUT_CAST:
            return

        if not self._subject_is_dressed(keywords):
            return

        existing = (request.negative_prompt or "").strip().rstrip(",")
        additions = [n for n in RATING_NEGATIVES if n not in existing]
        if not additions:
            return

        request.negative_prompt = ", ".join(filter(None, [existing, *additions]))
        log.debug("rating_negatives.added", added=additions)

    async def _add_sex_tags(
        self, keywords: list[str], request: GenerationRequest
    ) -> list[str]:
        """
        Put booru sex tags at the front of the prompt.

        Front, because attention thins across the prompt: the appearance anchor and the
        action already sit well past the first CLIP chunk, and a sex tag buried there does
        not hold against `solo` and `looking at viewer` near the start.
        """
        sex = await self._subject_sex(keywords, request)
        if not sex:
            return keywords

        tags = [t for t in SEX_TAGS.get(sex, ()) if t not in keywords]
        if not tags:
            return keywords

        log.debug("sex_tags.added", sex=sex, added=tags)
        return [*tags, *keywords]

    async def _add_sex_negatives(
        self, keywords: list[str], request: GenerationRequest
    ) -> None:
        """
        Negate the checkpoint's default sex, and nudity when the subject is dressed.

        `_add_species_negatives` does this for non-human species but returns early for
        humans, so a human subject previously had nothing at all pushing back.
        """
        sex = await self._subject_sex(keywords, request)
        if not sex:
            return

        existing = (request.negative_prompt or "").strip().rstrip(",")

        # Filtered against what has already been collected as well as what is already in
        # the prompt: the two sets overlap on purpose, so checking only `existing` would
        # append the same tag twice in a single call.
        additions: list[str] = []

        def collect(candidates) -> None:
            for candidate in candidates:
                if candidate not in existing and candidate not in additions:
                    additions.append(candidate)

        collect(SEX_NEGATIVES.get(sex, ()))

        dressed = self._subject_is_dressed(keywords)
        if dressed:
            collect(NUDITY_NEGATIVES)

        if not additions:
            return

        request.negative_prompt = ", ".join(filter(None, [existing, *additions]))
        log.debug("sex_negatives.added", sex=sex, dressed=dressed, added=additions)

    async def _add_species_negatives(
        self, keywords: list[str], request: GenerationRequest
    ) -> None:
        """
        Push back against the checkpoint's human prior for a non-human subject.

        A model trained overwhelmingly on humans renders "deep violet skin" as a human
        with a faint tint. Saying so in the positive prompt is not enough; the negative
        prompt is the other half of the same instruction.
        """
        scene = active_scene.get()
        if not scene or request.vis_type in VIS_TYPES_WITHOUT_CAST:
            return

        # The subject actually being drawn, not simply the first in the cast. Negatives
        # from a second character would fight the one on screen.
        await self._primary_and_secondary(keywords, request)
        primary = getattr(self, "_primary_character", None)
        if not primary:
            return

        species = (primary.base_attributes or {}).get("species", "")
        if not species or species.strip().lower().rstrip("s") in ("human", "man"):
            return

        additions = [n for n in SPECIES_NEGATIVES if n not in (request.negative_prompt or "")]
        if not additions:
            return

        existing = (request.negative_prompt or "").strip().rstrip(",")
        request.negative_prompt = ", ".join(filter(None, [existing, *additions]))
        log.debug(
            "species_negatives.added",
            species=species,
            character=primary.name,
            added=additions,
        )

    async def _suppress_stale_wardrobe(
        self, keywords: list[str], request: GenerationRequest
    ) -> list[str]:
        """
        Drop a character's cached wardrobe when the scene has already said what they wear.

        The cached wardrobe comes from a reinforcement refreshed every N turns. What the
        scene says is from this moment. When they disagree the scene wins, and they
        disagree often - undressing is a single beat, and the reinforcement will not
        notice for several more.

        Dedupe cannot do this job. "naked" and "utility suit" are not duplicates of each
        other, they are a contradiction, and first-occurrence-wins would keep the wrong
        one.
        """
        scene = active_scene.get()
        if not scene or request.vis_type in VIS_TYPES_WITHOUT_CAST:
            return keywords

        characters = list(getattr(self, "characters", None) or scene.characters)
        wardrobes = {
            token.strip().lower()
            for character in characters
            if character.visual_wardrobe
            for token in character.visual_wardrobe.split(",")
            if token.strip()
        }
        if not wardrobes:
            return keywords

        # Anything the scene itself says about clothing or state. Our own cached wardrobe
        # tokens are excluded, or they would count as evidence against themselves.
        scene_says_clothing = any(
            _is_wardrobe_token(kw) and kw.strip().lower() not in wardrobes
            for kw in keywords
        )
        if not scene_says_clothing:
            return keywords

        kept = [kw for kw in keywords if kw.strip().lower() not in wardrobes]
        if len(kept) != len(keywords):
            log.debug(
                "suppress_stale_wardrobe",
                dropped=len(keywords) - len(kept),
                reason="scene supplied its own clothing or state",
            )
        return kept

    async def _drop_duplicate_setting(
        self, keywords: list[str], request: GenerationRequest
    ) -> list[str]:
        """
        Drop the LLM's own setting keywords once a location anchor has supplied them.

        The setting was being described twice - "control room, spaceship interior, ..."
        from the anchor and "Starship bridge, viewport, sterile metal" from the LLM - in
        words that were not duplicates, so dedupe never caught them, and that sometimes
        disagreed outright. The anchor is the stable version, so it wins.

        Only removes tokens that are *about* the setting. Anything describing the action
        or the moment survives, since that is what the LLM is actually for.
        """
        scene = active_scene.get()
        if not scene:
            return keywords

        scene_tokens, _ = await self._anchor_token_sets(request)
        if not scene_tokens:
            return keywords

        # Distinctive words the anchor already established. A later keyword built from
        # the same vocabulary is re-describing the same place.
        anchor_words = {
            word
            for token in scene_tokens
            for word in re.findall(r"[\w'-]+", token.lower())
            if len(word) > 3
        }
        if not anchor_words:
            return keywords

        kept, dropped = [], []
        for keyword in keywords:
            lowered = keyword.strip().lower()
            if lowered in scene_tokens:
                kept.append(keyword)
                continue
            words = {w for w in re.findall(r"[\w'-]+", lowered) if len(w) > 3}
            if not words:
                kept.append(keyword)
                continue

            # Never drop something describing a person or an action, however much
            # vocabulary it shares with the setting. A long setting anchor is full of
            # common nouns - console, metal, space - and matching on those alone ate
            # legitimate action detail like "leaning over console".
            if _describes_action(lowered):
                kept.append(keyword)
                continue

            # Majority overlap rather than a strict subset. "Starship bridge" against an
            # anchor holding "starship interior" is the same place named again, and a
            # subset test misses it because "bridge" is a new word.
            overlap = len(words & anchor_words) / len(words)
            if overlap >= 0.5:
                dropped.append(keyword)
            else:
                kept.append(keyword)

        if dropped:
            log.debug("drop_duplicate_setting", dropped=dropped)
        return kept

    async def _anchor_token_sets(
        self, request: GenerationRequest
    ) -> tuple[set[str], list[set[str]]]:
        """
        The lowercased anchor tokens, by role: (scene, [per character]).

        Needed because trimming has to know what it is dropping. Anchors are cached, so
        this costs nothing.
        """
        scene = active_scene.get()
        if not scene:
            return set(), []

        scene_tokens: set[str] = set()
        scene_anchor = await self.scene_anchor(scene)
        if scene_anchor:
            scene_tokens = {t.strip().lower() for t in scene_anchor.split(",")}

        character_tokens: list[set[str]] = []
        if request.vis_type not in VIS_TYPES_WITHOUT_CAST:
            characters = list(getattr(self, "characters", None) or scene.characters)
            for character in characters:
                tokens = set()
                anchor = await self.character_anchor(character)
                if anchor:
                    tokens.update(t.strip().lower() for t in anchor.split(","))
                if character.visual_rules:
                    tokens.update(
                        t.strip().lower() for t in character.visual_rules.split(",")
                    )
                if tokens:
                    character_tokens.append(tokens)

        return scene_tokens, character_tokens

    async def _trim_to_budget(
        self, keywords: list[str], request: GenerationRequest
    ) -> list[str]:
        """
        Trim to the token budget while keeping the prompt a picture of something.

        The naive version of this trimmed only from the tail, on the theory that styles
        and anchors sit at the front and the LLM's action detail is the cheapest thing to
        lose. Against real data that was wrong: three derived anchors filled the entire
        budget on their own, so tail-trimming deleted every action keyword and left an
        accurate character standing in an accurate room doing nothing.

        So action keywords get a reserved share. Order of sacrifice:

        1. action keywords down to the reserve
        2. character anchors past the first - fewer right-looking people beats several
        3. the scene anchor
        4. only then, the reserve itself
        """
        # Emphasis is rendered after this runs, and its brackets and weight cost tokens
        # the budget check would otherwise miss - observed landing at 81 against 77.
        budget = min(
            self._max_prompt_tokens(),
            self.resolve_prompt_profile(request).max_prompt_tokens,
        )
        if abs(self._identity_weight() - 1.0) >= 0.01:
            budget -= EMPHASIS_TOKEN_OVERHEAD

        if estimate_prompt_tokens(", ".join(keywords)) <= budget:
            return keywords

        scene_tokens, character_tokens = await self._anchor_token_sets(request)
        anchor_tokens = set(scene_tokens)
        for tokens in character_tokens:
            anchor_tokens |= tokens

        def over() -> bool:
            return estimate_prompt_tokens(", ".join(keywords)) > budget

        def is_action(kw: str) -> bool:
            return kw.strip().lower() not in anchor_tokens

        # 1. trim action from the tail, but stop at the reserve
        reserve = max(1, int(budget * ACTION_BUDGET_RESERVE))
        while over():
            action = [kw for kw in keywords if is_action(kw)]
            if estimate_prompt_tokens(", ".join(action)) <= reserve:
                break
            for index in range(len(keywords) - 1, -1, -1):
                if is_action(keywords[index]):
                    del keywords[index]
                    break
            else:
                break

        # 2. character anchors past the first, last one first
        for tokens in reversed(character_tokens[1:]):
            if not over():
                break
            keywords = [kw for kw in keywords if kw.strip().lower() not in tokens]
            log.debug("prompt_budget.dropped_character_anchor")

        # 3. the scene anchor
        if over() and scene_tokens:
            keywords = [kw for kw in keywords if kw.strip().lower() not in scene_tokens]
            log.debug("prompt_budget.dropped_scene_anchor")

        # 4. the reserve, as a last resort
        while keywords and over():
            keywords = keywords[:-1]

        return keywords

    @set_processing
    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        # Before _finalize_prompt, which selects its keyword handling from whichever
        # backend the request is bound for - and attaching a reference is what moves a
        # request onto the edit backend.
        await self.attach_character_references(request)

        # A background reference alone also needs the reference-conditioned
        # workflow; attach_character_references only reroutes for subject refs.
        if (
            request.background_reference_assets
            and request.gen_type == GEN_TYPE.TEXT_TO_IMAGE
            and self.can_edit_images
        ):
            request.gen_type = GEN_TYPE.IMAGE_EDIT
        self._apply_seed(request)
        await self._finalize_prompt(request)

        response = GenerationResponse(
            request=request,
            id=request.id,
        )

        emission = GenerationEmission(
            agent=self,
            request=request,
            response=response,
        )

        await async_signals.get("agent.visual.generation.before_generate").send(
            emission
        )

        async def on_done(fut: asyncio.Future[GenerationResponse]):
            response = fut.result()
            await self._auto_save_generated_asset(request=request, response=response)
            log.debug(
                "image_generated",
                request=response.request.model_dump(exclude={"inline_reference"}),
                base64=response.base64[:100] if response.base64 else None,
            )
            emit(
                "image_generated",
                websocket_passthrough=True,
                data=response.model_dump(),
            )
            if request.callback:
                await request.callback(response=response)
            await async_signals.get("agent.visual.generation.after_generate").send(
                emission
            )

        # if reference images are provided we route to image edit
        # otherwise we route to text to image
        if request.gen_type == GEN_TYPE.IMAGE_EDIT:
            await self.generate_image_edit(request, response, on_done)
        else:
            await self.generate_text_to_image(request, response, on_done)

        return response

    async def generate_text_to_image(
        self,
        request: GenerationRequest,
        response: GenerationResponse,
        on_done: Callable[[GenerationResponse], None],
    ):
        if not self.can_generate_images:
            raise TextToImageNotAvailableError("Text to image is not available")

        backend = self.backend
        fn = getattr(self, f"{self.backend.name}_prepare_generation", None)
        if fn:
            backend = await fn(request)

        response.backend_name = backend.name

        # Routed through the VRAM handoff so an enabled toggle can clear the text
        # model off the GPU for the duration of the generation.
        task = asyncio.create_task(self._backend_generate(backend, request, response))
        task.add_done_callback(lambda fut: asyncio.create_task(on_done(fut)))

        # Track task for cancellation support
        self._track_generation_task(task, backend)

        await self.set_background_processing(task, self.on_image_generation_error)

    async def generate_image_edit(
        self,
        request: GenerationRequest,
        response: GenerationResponse,
        on_done: Callable[[GenerationResponse], None],
    ):
        if not self.can_edit_images:
            raise ImageEditNotAvailableError("Image edit is not available")

        backend = self.backend_image_edit
        fn = getattr(self, f"{self.backend_image_edit.name}_prepare_generation", None)

        if fn:
            backend = await fn(request)

        response.backend_name = backend.name

        # Same handoff routing as text-to-image; edit workflows hold even more VRAM.
        task = asyncio.create_task(self._backend_generate(backend, request, response))
        task.add_done_callback(lambda fut: asyncio.create_task(on_done(fut)))

        # Track task for cancellation support
        self._track_generation_task(task, backend)

        await self.set_background_processing(task, self.on_image_generation_error)

    async def cancel_generation(self):
        """
        Cancel all active image generation tasks.
        """
        if (
            not hasattr(self, "_active_generation_tasks")
            or not self._active_generation_tasks
        ):
            log.debug("cancel_generation", cancelled=False, reason="no_active_tasks")
            return

        # Collect all active tasks and their backends
        active_tasks = [
            task for task in self._active_generation_tasks if not task.done()
        ]

        if not active_tasks:
            log.debug("cancel_generation", cancelled=False, reason="no_active_tasks")
            return

        # Collect unique backends to call cancel_request on
        # Use dict with backend name/id as key to ensure uniqueness
        backends_to_cancel = {}
        for task in active_tasks:
            if hasattr(self, "_generation_task_backends"):
                backend = self._generation_task_backends.get(task)
                if backend:
                    # Use backend name as key for uniqueness
                    backend_key = getattr(backend, "name", id(backend))
                    backends_to_cancel[backend_key] = backend

        # Cancel all tasks
        cancelled_count = 0
        for task in active_tasks:
            if not task.done():
                task.cancel()
                cancelled_count += 1

        # Call cancel_request on all unique backends
        for backend in backends_to_cancel.values():
            try:
                await backend.cancel_request()
            except Exception as e:
                log.error(
                    "cancel_generation.backend_cancel_failed",
                    error=str(e),
                    backend=backend.name if hasattr(backend, "name") else str(backend),
                )

        log.info(
            "cancel_generation",
            cancelled=True,
            task_count=cancelled_count,
            backend_count=len(backends_to_cancel),
        )
