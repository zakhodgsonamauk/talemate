# Plan: visual-consistency

**Spec**: `conductor/tracks/visual-consistency/spec.md`
**Design doc**: `docs/fork/visual-consistency-design.md`

Python side is TDD — `tests/` has a real pytest suite (`testpaths = ["tests"]`,
`asyncio_mode = "auto"`). Frontend has no test runner, so P5 is verified in the browser
via Playwright (same approach the url-state-sync track landed on).

Commit after each task.

---

## Phase 0 — Persisted data

### T1 — `Character.visual_anchor`

- [ ] Failing test `tests/test_visual_anchor.py`: a `Character` round-trips
      `visual_anchor` through `model_dump()` / re-construction, and defaults to `None`.
- [ ] Add `visual_anchor: str | None = None` to `Character`
      (`src/talemate/character.py:48`, beside `visual_rules`). Pydantic handles
      serialization — no save/load change needed for characters.
- [ ] Add `WorldStateManager.update_character_visual_anchor`
      (`src/talemate/world_state/manager.py`, beside
      `update_character_visual_rules:438`) — same shape, sets `memory_dirty = True`.
- [ ] Verify: `pytest tests/test_visual_anchor.py tests/test_character.py`

**AC**: field persists; existing character tests unaffected.

### T2 — `Scene.visual_anchor`

Scene is not a pydantic model, so every site that touches `visual_style_template` needs
the parallel entry:

- [ ] Failing test: scene serialize → load round-trips `visual_anchor`.
- [ ] `src/talemate/tale_mate.py:157` — `self.visual_anchor = None` in `__init__`
- [ ] `src/talemate/tale_mate.py:1425` — status emit payload
- [ ] `src/talemate/tale_mate.py:2150` — serializer
- [ ] `src/talemate/load/__init__.py:317` — load, defaulting to `""`/`None`
- [ ] `src/talemate/world_state/manager.py:1133` — `update_scene_settings` parameter
- [ ] Verify: round-trip test + `pytest tests/` for scene load/save regressions

**AC**: hand-editing `visual_anchor` in a scene JSON survives a load/save cycle.

---

## Phase 1 — Derivation

### T3 — `visual/derive-visual-anchor.jinja2`

- [ ] New template returning `<ANCHOR>comma, delimited, keywords</ANCHOR>`.
- [ ] Two modes: character (input = `base_attributes.appearance` + `description`
      fallback) and scene (input = `scene.description` + `scene.context`).
- [ ] Constraints stated in the template: physically visible traits only; no
      personality, plot, camera language, or art style; 10-14 tokens for a character,
      8-10 for a scene.
- [ ] Verify: `tests/prompts` pattern — assert the rendered template contains the
      appearance prose and the constraint text.

**AC**: template renders for both modes without a live LLM.

### T4 — Derive-and-cache

- [ ] Failing tests, LLM call stubbed:
      - anchor already set → zero LLM calls, value returned verbatim
      - anchor unset, appearance present → one call, result written back to
        `character.visual_anchor`
      - anchor unset, appearance absent → `None`, warning logged, no crash
- [ ] Implement in a new `src/talemate/agents/visual/anchors.py`:
      `async def character_anchor(character) -> str | None` and
      `async def scene_anchor(scene) -> str | None`.
- [ ] Strip the `<ANCHOR>` wrapper, normalise whitespace, collapse to
      comma-space-joined tokens, dedupe.
- [ ] Verify: `pytest tests/test_visual_anchor.py`

**AC**: second call for the same character issues no LLM request.

---

## Phase 2 — Deterministic assembly

All of Phase 2 lands in `src/talemate/agents/visual/style.py` +
`src/talemate/agents/visual/anchors.py`. **`generate-visual-asset.json` is not
touched.**

### T5 — In-frame matcher

- [ ] Failing tests:
      - full name in the descriptive prose → matched
      - first name only → matched
      - name absent → not matched
      - name containing a space and an apostrophe → matched, regex-safe
      - descriptive part missing → falls back to all active characters
      - 4 characters matched → 3 kept, most-mentioned first, drop logged
- [ ] `characters_in_frame(prompt, scene) -> list[Character]`, scanning
      `positive_descriptive` across the LLM parts. Case-insensitive, word-boundary,
      `re.escape` on names.

**Verified during plan evaluation**: the LLM emits one `VisualPromptPart` (graph node
`8cfcb710`) carrying **both** `positive_keywords_raw` (from `local.keywords_prompt`)
and `positive_descriptive` (from `local.dewscriptive_prompt`). So the prose T5 needs
*is* populated in KEYWORDS mode — it is generated and then simply unused downstream.
No graph change needed to reach it.

The `Instructions` box in the review UI is a different thing: part `ce9cc3cf`'s
`instructions`, fed from the module's `local.instructions` input. Do not confuse the
two.
- [ ] Verify: `pytest tests/test_visual_anchor.py -k in_frame`

**AC**: all six cases pass.

### T6 — Anchor insertion in `apply_styles`

- [ ] Failing test: given a `VisualPrompt` carrying an LLM part, after `apply_styles`
      the positive prompt reads
      `art style, vis_type style, scene anchor, char anchors + visual_rules, LLM
      keywords` in that order.
- [ ] Extend `StyleMixin.apply_styles` (`style.py:160`). Anchors are inserted **after**
      the two style parts and **before** the LLM parts — `_build_prompt` dedupe keeps
      first occurrence (`schema.py:168`), so position is what makes anchors win over a
      duplicate LLM token.
- [ ] Each matched character's `visual_rules` is appended to that character's anchor
      part. **This is what fixes RC4.**
- [ ] Build on `apply_styles` (plural). `apply_style` (singular, `style.py:150`) is
      broken — see hazards.
- [ ] Verify: `pytest tests/test_visual_anchor.py -k apply_styles`

**AC1** (byte-stable identity), **AC4** (`visual_rules` honoured), **AC5** (off-screen
excluded).

### T7 — Sanitiser

- [ ] Failing test: an LLM part containing every blocklist entry emerges with none of
      them; anchor and style tokens containing a blocklisted substring are untouched.
- [ ] Blocklist per design D4 — format/camera meta and non-visual abstractions.
      Dropped tokens logged.
- [ ] **Identifying the LLM part exactly**: snapshot `pre_existing = list(prompt.parts)`
      at entry to `apply_styles`. Those are the LLM's parts; everything inserted after
      is ours. Sanitise `positive_keywords_raw` on the snapshot only, and leave their
      `positive_descriptive` alone — T5 reads it, and it is also what a DESCRIPTIVE
      backend would ship.
- [ ] Verify: `pytest tests/test_visual_anchor.py -k sanitise`

**AC3**.

### T8 — Prompt token cap

- [ ] Failing test: an over-budget prompt truncates LLM keywords first, then character
      anchors beyond the first, then the scene anchor. Styles and the first character
      anchor always survive.
- [ ] New `AgentActionConfig` in `prompt_generation`
      (`src/talemate/agents/visual/agent.py:167` block) — a **separate** setting.
      `prompt_generation.max_length` is the LLM's budget, not an image-prompt cap.
- [ ] Verify: `pytest tests/test_visual_anchor.py -k budget`

**AC6**.

---

## Phase 3 — Template rewrite

### T9 — `generate-image-SCENE_ILLUSTRATION.jinja2`

- [ ] Delete the per-character appearance `batch_query_scene` queries (lines 6, 11).
      Appearance comes from anchors now. **Removes N LLM calls per image.**
- [ ] Delete line 52 and the framing / orientation / avoid-portrait-and-square
      requirement lines. Resolution already fixes format; restating it is what
      produced `horizontal, landscape, cinematic, dynamic`.
- [ ] Keep the scene-action query. Add the rule: name the concrete location and genre;
      physically visible nouns only; no plot, state, or emotion words.
- [ ] Verify: render the template in a test, assert the deleted strings are gone and
      the new rule is present.

**AC2**, and prevention for **AC3**.

### T10 — `generate-image-prompt-type.jinja2`

- [ ] Add the visible-nouns-only constraint and state the token budget.
- [ ] Verify: rendered-template assertion.

---

## Phase 4 — Seed

### T11 — `SamplerSettings.seed` + `seed_mode`

- [ ] Failing tests: `seed_mode: random` → payload seed absent or `-1`;
      `seed_mode: scene` → two builds for one scene produce an identical seed;
      `seed_mode: fixed` → configured value used.
- [ ] `SamplerSettings.seed: int | None` (`schema.py:238`).
- [ ] Thread into the A1111 payload (`backends/automatic1111.py:134-143`).
- [ ] `seed_mode` config option; default `random` so behaviour is unchanged until
      opted in.
- [ ] Record the resolved seed in asset metadata so **AC8** is observable in the UI.
- [ ] Verify: `pytest tests/ -k seed`

**AC8**. Scope note in the design doc: seed is a style lever, not an identity lever.

---

## Phase 5 — UI

No frontend test runner. Verified in the browser.

### T12 — Websocket plumbing

- [ ] `handle_update_character_visual_anchor` in
      `src/talemate/server/world_state_manager/character.py`, mirroring
      `handle_update_character_visual_rules:121` — payload model, error path,
      re-emit character details, `signal_operation_done`.
- [ ] Scene anchor rides the existing `update_scene_settings` action (T2).
- [ ] A `derive_visual_anchor` action calling T4 and emitting the result.
- [ ] Verify: `tests/server` pattern if one fits; otherwise browser round-trip in T15.

### T13 — Character anchor UI

- [ ] Extend `talemate_frontend/src/components/WorldStateManagerCharacterVisualsRules.vue`
      — anchor textarea beside the rules field, plus a Derive button.
- [ ] Verify: browser.

### T14 — Scene anchor UI

- [ ] Anchor field beside the existing visual style template control in scene settings,
      plus a Derive button.
- [ ] Verify: browser.

**AC7** is proven in T15.

---

## Phase 6 — Verification

### T15 — Playwright E2E

Against the running app, review queue at
`#/s/infinity-quest-dynamic-story-v2/main?save=Infinity+Quest+1.json&visual=review`.

- [ ] Generate two illustrations back to back; assert the character-anchor substring is
      byte-identical (**AC1**).
- [ ] Assert a setting token naming location type and genre is present (**AC2**).
- [ ] Assert no blocklist entry appears (**AC3**).
- [ ] Assert Elmer's face-in-shadow rule is present when Elmer is in frame (**AC4**).
- [ ] Generate with a character off-screen; assert no anchor for them (**AC5**).
- [ ] Assert the prompt is within the T8 cap (**AC6**).
- [ ] Edit an anchor in the world editor, save, reload, confirm persistence, regenerate
      and confirm the edited value is used (**AC7**).
- [ ] `seed_mode: scene` → two generations report the same seed; `random` → they differ
      (**AC8**).
- [ ] Screenshot the review queue as evidence (**AC9**).

**Backend note**: `backend_image_edit` is empty in `config.yaml`. Test the
`TEXT_TO_IMAGE` path only.

### T16 — Docs

- [ ] `FORK.md` divergence rows for every upstream file touched.
- [ ] Design doc status → implemented; record any amendment made during execution
      (the url-state-sync spec's amendment note is the format to follow).
- [ ] `conductor/decision-log.md` entry.

---

## DAG

```
T1 ─┬─> T4 ─┬─> T6 ─> T7 ─> T8 ─┬─> T15 ─> T16
T2 ─┘       │     ^              │
T3 ─────────┘     │              │
T5 ───────────────┘              │
T9  ─────────────────────────────┤
T10 ─────────────────────────────┤
T11 ─────────────────────────────┤
T12 ─> T13 ──────────────────────┤
T12 ─> T14 ──────────────────────┘
```

Parallel groups:

| Group | Tasks | Note |
|---|---|---|
| G1 | T1, T2, T3 | independent — data fields and the new template |
| G2 | T4, T5 | T4 needs T1+T2+T3; T5 needs nothing but lands in the same module |
| G3 | T6 → T7 → T8 | strictly sequential, all in `apply_styles` |
| G4 | T9, T10, T11, T12 | independent of G3 |
| G5 | T13, T14 | need T12 |
| G6 | T15 | needs everything |
| G7 | T16 | needs T15 |

## Complexity

**L** — 16 tasks, 6 phases. Python is well-fenced (one choke point, real test suite).
Cost sits in P5 (frontend, no runner) and T15 (E2E needs a live image backend at
`localhost:5001`).

## Hazards

Carried from the design doc — do not rediscover:

- `StyleMixin.apply_style` (singular, `style.py:150`) raises `AttributeError`: it hands
  a template-id string to `style_template(vis_type: VIS_TYPE)`, which calls
  `vis_type.value.lower()`. Only caller is the `ApplyStyle` node (`nodes.py:382`), used
  by no shipped module. Build on `apply_styles`. Out of scope to fix.
- Prompt assembly is a 140-node graph. Every change here has a Python choke point
  instead — use it.
- `_build_prompt` dedupe keeps first occurrence (`schema.py:168`). Insertion order is
  load-bearing.
- `prompt_generation.max_length` = 1024 is the LLM budget. Do not overload it.
- `backend_image_edit: ''` — `IMAGE_EDIT` unavailable. Don't route tests through it.
- Character names contain spaces and apostrophes — `re.escape` in the matcher.
- Generated images and `scenes/*/assets/library.json` churn are disposable test output.
  Don't preserve them, don't blind-revert them.
