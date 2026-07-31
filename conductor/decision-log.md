# Decision Log

Technical and business decisions made during development.

## Format

### DECISION-XXX: [Title]
- **Date**: YYYY-MM-DD
- **Track**: track-id
- **Decision**: What was decided
- **Rationale**: Why this decision was made
- **Alternatives**: What else was considered
- **Impact**: Effects on architecture/product/business

### DECISION-001: Character consistency comes from cached anchors, not from re-derivation
- **Date**: 2026-07-30
- **Track**: visual-consistency
- **Decision**: Derive each character's appearance keywords once from
  `base_attributes.appearance`, cache them on `Character.visual_anchor`, and inject that
  string verbatim into every image prompt the character appears in. Same treatment for
  the scene's setting.
- **Rationale**: The generation template asked the LLM to re-describe appearance on
  every image. A non-deterministic source cannot produce a stable subject, so the same
  character arrived as a different person each time. Caching also removes one LLM call
  per character per image.
- **Alternatives**: A code-side prose parser (brittle on "four-fingered hands" and
  similar); injecting the prose verbatim (~50 CLIP tokens per character and reads badly
  to SDXL); reference images via IP-Adapter or `IMAGE_EDIT` (the strongest lever, but it
  needs a second backend running — deferred, not rejected).
- **Impact**: Two new persisted fields and one new prompt template. Illustrations get
  cheaper, not dearer.

### DECISION-002: Prompt filtering happens immediately before dispatch
- **Date**: 2026-07-30
- **Track**: visual-consistency
- **Decision**: Sanitising, in-frame pruning and budget enforcement live in
  `GenerationMixin._finalize_prompt`, not in `StyleMixin.apply_styles`.
- **Rationale**: The node graph builds the `VisualPrompt` empty, lets `apply_styles` add
  styles and anchors, then appends the LLM's keywords. `apply_styles` never sees the
  LLM's output, so filtering there did nothing at all. Found by reading a live A1111
  payload; the unit tests missed it because they fed `apply_styles` a prompt shape the
  real graph never produces.
- **Alternatives**: Editing the 140-node `generate-visual-asset.json` to reorder stages
  (high risk, hand-edited JSON); filtering inside `VisualPrompt._build_prompt` (cannot
  tell which part is the LLM's).
- **Impact**: A test fixture that mirrors an assumption cannot falsify it. Fixtures for
  this area now build a real `VisualAgent`.

### DECISION-003: Action keywords hold a reserved share of the prompt budget
- **Date**: 2026-07-30
- **Track**: visual-consistency
- **Decision**: 35% of the image-prompt budget is reserved for what is happening in the
  shot. Extra character anchors are dropped before that reserve is touched. Default
  budget 250 tokens, up from 150.
- **Rationale**: The original drop order sacrificed action first, on the theory that
  identity matters more. Against real anchors that was self-defeating: three anchors plus
  style tags reached ~155 tokens alone, so trimming removed every action keyword and
  produced accurate people standing in an accurate room doing nothing.
- **Alternatives**: Raising the budget alone (anchors would still crowd out action as
  casts grow); shortening anchors (fights the LLM's natural phrasing).
- **Impact**: `ACTION_BUDGET_RESERVE` in `agents/visual/generation.py`.

### DECISION-004: Seed pinning is scoped as a style lever, not an identity lever
- **Date**: 2026-07-30
- **Track**: visual-consistency
- **Decision**: Ship `seed_mode` (Random / Per scene / Fixed), defaulting to Random, and
  say plainly in the setting's own help text that it does not keep faces consistent.
- **Rationale**: In txt2img a seed applies to the whole image, not per character. It
  steadies palette and rendering across a scene's illustrations; identity comes from
  anchors. Promising more would misrepresent it.
- **Alternatives**: Omitting seed control (it is genuinely useful for look consistency);
  implying it fixes identity (untrue).
- **Impact**: `SEED_MODE` and `resolve_seed` in `agents/visual/schema.py`. sha256 over
  the scene id rather than `hash()`, which is salted per process and would break the one
  property the mode exists to provide.

### DECISION-005: Scene direction gate widened with a minimal OR, not rewired
- **Date**: 2026-07-31
- **Track**: director-trust-and-levers
- **Decision**: In `scene-loop.json`, the AND router feeding `Scene Direction.state`
  now takes `(GetSceneState.auto_progress OR GetSceneIntent.direction_always_on)` as
  its first flag (new `core/ORRouter` node `3919355b-3e5c-4328-a75b-de0c6fecee67`)
  instead of `auto_progress` alone. The second flag (`NOT shared.skip_to_player`) is
  untouched. Effect: with the scene intent's `direction_always_on` set, the director
  keeps taking direction turns during manual play (auto_progress off), while explicit
  skip-to-player still suppresses it.
- **Rationale**: Live-play failure: with Scene Direction enabled but auto_progress
  off, the director never got a turn, so player-initiated stakes were never
  adjudicated. `direction_always_on` was already wired to the SceneDirection node's
  `always_on` input but the node's `state` gate never fired without auto_progress.
- **Alternatives**: Deep scene-loop rework (AI-turn caps, hand-back triggers) —
  deliberately NOT done here; that is owned by the `autonomous-story` track (its R2/R3).
  This is the single minimal change director-trust-and-levers makes to the loop, so the
  autonomous-story implementer must build on top of this OR gate rather than replace it.
- **Impact**: Direction turns can now occur in manual play sessions. Frequency and
  stance levers (same track) bound how intrusive that is.
