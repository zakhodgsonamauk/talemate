import asyncio
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
from .anchors import _mention_count
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
    sanitise_keywords,
)
from .exceptions import ImageEditNotAvailableError, TextToImageNotAvailableError

log = structlog.get_logger("talemate.agents.visual.generation")

# Share of the prompt budget held back for what is happening in the shot. Without a
# reserve, three derived character anchors fill the whole budget and the image ends up
# depicting nobody doing anything.
ACTION_BUDGET_RESERVE = 0.35

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
        keywords = [kw.strip() for kw in original.split(",") if kw.strip()]

        keywords = sanitise_keywords(keywords)
        keywords = await self._drop_absent_character_anchors(keywords, request)
        keywords = await self._trim_to_budget(keywords, request)

        request.prompt = ", ".join(dict.fromkeys(keywords))

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
        budget = self._max_prompt_tokens()
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
