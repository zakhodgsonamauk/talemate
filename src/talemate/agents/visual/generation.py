import asyncio
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
from .anchors import _is_wardrobe_token, _mention_count
from .schema import (
    GEN_TYPE,
    PROMPT_TYPE,
    SEED_MODE,
    GenerationResponse,
    BackendStatusType,
    GenerationRequest,
    Resolution,
    FORMAT_TYPE,
    resolve_seed,
)
from .style import (
    VIS_TYPES_WITHOUT_CAST,
    estimate_prompt_tokens,
    normalize_keyword,
    sanitise_keywords,
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

        original = request.prompt
        keywords = [
            normalize_keyword(kw) for kw in original.split(",") if kw.strip()
        ]

        keywords = sanitise_keywords(keywords)
        keywords = await self._drop_absent_character_anchors(keywords, request)
        keywords = await self._suppress_stale_wardrobe(keywords, request)
        keywords = await self._drop_duplicate_setting(keywords, request)
        keywords = await self._drop_secondary_traits(keywords, request)
        keywords = await self._trim_to_budget(keywords, request)

        keywords = list(dict.fromkeys(keywords))
        await self._add_species_negatives(keywords, request)
        request.prompt = await self._render_with_emphasis(keywords, request)

        if request.prompt != original:
            log.debug(
                "finalize_prompt",
                before=estimate_prompt_tokens(original),
                after=estimate_prompt_tokens(request.prompt),
            )

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

        scored: list[tuple[int, set[str], object]] = []
        for character in characters:
            tokens = set()
            anchor = await self.character_anchor(character)
            if anchor:
                tokens.update(t.strip().lower() for t in anchor.split(","))
            if not tokens:
                continue
            scored.append((len(tokens & present), tokens, character))

        if not scored:
            return set(), set()

        scored.sort(key=lambda row: row[0], reverse=True)
        primary_hits, primary_tokens, primary = scored[0]

        if not primary_hits:
            # Nobody's anchor is in the prompt - nothing to emphasise or strip.
            return set(), set()

        secondary: set[str] = set()
        for _, tokens, _character in scored[1:]:
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
        secondary_words = words_of(secondary)
        primary_words = words_of(primary)
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
        budget = self._max_prompt_tokens()
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

        task = asyncio.create_task(backend.generate(request, response))
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

        task = asyncio.create_task(backend.generate(request, response))
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
