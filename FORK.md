# FORK — divergence from upstream

Merge-conflict early warning system. Every upstream file we modify gets a row
here, with why. New files we add are listed separately — they carry no merge risk.

**Upstream:** [`vegu-ai/talemate`](https://github.com/vegu-ai/talemate) (AGPL-3.0)
**Our fork:** `zakhodgsonamauk/talemate`
**Fork point:** `c12a82930e913816fdac21aedada1962ac45c3d7` — tag `0.38.0`, 2026-07-02
**Working branch:** `feature/tell-me-a-story` off `main`

Keep `main` a clean mirror of `upstream/main`. Sync with:

```bash
git fetch upstream
git checkout main && git merge --ff-only upstream/main
git checkout feature/tell-me-a-story && git rebase main   # or merge
```

---

## Modified upstream files

| File | Lines touched | Why | Added |
|---|---|---|---|
| `docs/.pages` | +1 (after line 5) | Add `- Fork notes: fork` so our `docs/fork/` pages appear in the mkdocs nav. Root `.pages` uses an explicit nav list, so an unlisted directory is invisible. | 2026-07-29 |
| `talemate_frontend/src/components/TalemateApp.vue` | +16 / -1 (import at 415, `mixins:` at 453, snackbar after 363) | URL state sync: import + register `UrlStateMixin`, and the restore-notice snackbar. All logic lives in the mixin; this is registration only. | 2026-07-30 |
| `talemate_frontend/src/components/VisualLibrary.vue` | +23 / -5 (`expose:` at 602, `@update:detail-tab` at 156) | URL state sync: `expose` widened to `dialog`, `dialogModel`, `activeTab`, `sceneSelectedId`, `sceneInitialTab`, `sceneOpenNodes`, `open`, so the library's tab, selection, detail sub-tab and tree expansion are all addressable; owns the detail sub-tab reported up from the scene panel. | 2026-07-30 |
| `talemate_frontend/src/components/VisualImageView.vue` | +9 / -1 (`emits:` at 259, new `activeTab` watcher at ~275) | URL state sync: emit `update:active-tab` so an ancestor can own which asset sub-tab (info/reference/cover_crop) is showing. Emitted from a watcher, so the component's own jumps to Reference during analysis also reach the URL. | 2026-07-30 |
| `talemate_frontend/src/components/VisualLibraryScene.vue` | +2 / -1 (`emits:` at 167, listener at ~97) | URL state sync: re-emit the child's tab change upward as `update:detail-tab`. Pure pass-through. | 2026-07-30 |
| `talemate_frontend/src/components/SceneMessages.vue` | +10 (`data()` at 534, `provide()` at 658) | URL state sync for story images: central `viewedAssetId` plus `getViewedAssetId` / `setViewedAssetId` provides. Needed because the viewer state was previously per-message. | 2026-07-30 |
| `talemate_frontend/src/components/MessageAssetImage.vue` | +33 / -9 (`inject`/`data`/`computed` at 79-118) | URL state sync for story images: `showAssetView` becomes a computed over the provided central id; `inject` switched to object form so the two new keys can default to `null`. | 2026-07-30 |
| `src/talemate/character.py` | +5 (`visual_anchor` at 49) | Visual consistency: cached appearance keywords, injected into every image the character appears in. Pydantic, so it serializes for free. | 2026-07-30 |
| `src/talemate/tale_mate.py` | +6 (`__init__` ~157, status emit ~1428, serializer ~2153) | Visual consistency: `Scene.visual_anchor`. Scene is a plain class, so each plumbing site needs its own entry. | 2026-07-30 |
| `src/talemate/load/__init__.py` | +3 (after 317) | Visual consistency: load `Scene.visual_anchor`, coercing `""` to `None` so "not derived yet" stays distinguishable. | 2026-07-30 |
| `src/talemate/world_state/manager.py` | +23 (`CharacterDetails` at 77, builder at 209, `update_character_visual_anchor` after 455, `update_scene_settings` at ~1140) | Visual consistency: anchor setters and exposure to the world editor. | 2026-07-30 |
| `src/talemate/server/world_state_manager/__init__.py` | +42 (`SceneSettingsPayload` at 186, `handle_derive_scene_visual_anchor` after 1159) | Visual consistency: scene anchor round-trip plus its derive action. | 2026-07-30 |
| `src/talemate/server/world_state_manager/character.py` | +92 (payloads at 29, two handlers after 147) | Visual consistency: character anchor update and derive actions, mirroring the `visual_rules` pair. | 2026-07-30 |
| `src/talemate/agents/visual/agent.py` | +25 / -2 (`AnchorMixin` at 24/56, `seed_mode`+`seed` at ~152, `image_max_tokens` at ~178) | Visual consistency: mix in anchors, add the image-prompt budget and seed settings. | 2026-07-30 |
| `src/talemate/agents/visual/style.py` | +190 / -3 (`apply_styles` now async, `_insert_anchors`, blocklist, `estimate_prompt_tokens`) | Visual consistency: anchors inserted ahead of the LLM's keywords; insertion order is load-bearing because `_build_prompt` dedupes first-occurrence-wins. | 2026-07-30 |
| `src/talemate/agents/visual/generation.py` | +170 (`_apply_seed`, `_finalize_prompt`, `_drop_absent_character_anchors`, `_trim_to_budget`) | Visual consistency: the only place the assembled prompt is complete, so sanitising, in-frame pruning and budget enforcement all happen here. See design doc amendment A1. | 2026-07-30 |
| `src/talemate/agents/visual/schema.py` | +45 (`SEED_MODE`, `resolve_seed`, `SamplerSettings.seed`) | Visual consistency: seed control. sha256 over the scene id, not `hash()`, which is per-process salted. | 2026-07-30 |
| `src/talemate/agents/visual/nodes.py` | +3 / -1 (line 350) | Visual consistency: `await` the now-async `apply_styles`. | 2026-07-30 |
| `src/talemate/agents/visual/backends/automatic1111.py` | +25 / -18 (`build_payload` extracted) | Visual consistency: pass the seed, and make the payload assertable without a live backend. | 2026-07-30 |
| `src/talemate/prompts/templates/visual/generate-image-SCENE_ILLUSTRATION.jinja2` | +18 / -24 | Visual consistency: dropped the per-character appearance query (the direct cause of the inconsistency, and one LLM call per character per image) and the format-emphasis instruction; added the demand to name the location and genre. | 2026-07-30 |
| `src/talemate/prompts/templates/visual/generate-image-prompt-type.jinja2` | +4 | Visual consistency: keywords must name something a camera could photograph; state the budget. | 2026-07-30 |
| `talemate_frontend/src/components/WorldStateManagerCharacterVisualsRules.vue` | +95 / -3 | Visual consistency: Appearance Keywords field plus its derive button. | 2026-07-30 |
| `talemate_frontend/src/components/WorldStateManagerSceneSettings.vue` | +55 / -1 | Visual consistency: Setting Keywords field plus derive. `visual_anchor` had to be added to **both** payload literals — that handler sends the whole settings set, so a field missing from either would be written back as null on any later change. | 2026-07-30 |
| `tests/data/graphs/results/test-harness-assets.json` | +1 | Visual consistency: baseline gains `"seed": null` from the new `SamplerSettings` field. Shape drift, not behaviour. | 2026-07-30 |

Conflict risk: **low.**

- `docs/.pages` — one appended nav line in a 5-line file. If upstream adds its own
  top-level section the merge is a trivial both-added.
- `TalemateApp.vue` — three small, well-separated hunks in a 1742-line file. This is
  the hottest file in the tree, so expect to re-apply by hand rather than cleanly
  merge. Deliberately kept to registration: the mixin hooks itself in via
  `registerMessageHandler` (`TalemateApp.vue:972`) instead of editing
  `handleMessage`, so upstream churn in the dispatcher does not conflict.
- `VisualLibrary.vue` — an `expose` array plus one event listener. Conflicts only if
  upstream edits the same array or that component tag; both merge obviously.
- `VisualImageView.vue` / `VisualLibraryScene.vue` — one emit declaration and one
  listener each, plus a watcher. Additive and self-contained. The contract they
  establish (`update:active-tab` → `update:detail-tab` → `sceneInitialTab`) is what
  makes the asset sub-tab readable; if upstream gives `VisualImageView` a
  `v-model:active-tab` of its own, delete our chain and bind to theirs.
- `SceneMessages.vue` — two small additive hunks (one data key, two provides) in a
  2100-line file. Low risk, but it is an actively-developed file.
- `MessageAssetImage.vue` — the only **behavioural** change we have made to an
  upstream component: the per-instance `showAssetView` flag became a computed over
  state owned by `SceneMessages`. If upstream reworks how the asset viewer is opened,
  re-apply by hand rather than trusting a clean merge. The fallback `localAssetViewOpen`
  keeps the component working if the provides ever disappear.

When this table gains rows, note the *upstream* line numbers at time of change so
a future rebase can find the hunk.

---

## Files we added

These do not conflict — but they can be *orphaned* by upstream refactors, so the
"depends on" column matters.

| Path | Purpose | Depends on (upstream contract) |
|---|---|---|
| `BRIEF.md` | Project brief | — |
| `ARCHITECTURE.md` | Subsystem map from Phase 0 recon | Line refs valid at `0.38.0` — restate on major bumps |
| `PLAN.md` | Phased implementation plan | — |
| `FORK.md` | This file | — |
| `docs/fork/images-without-comfyui.md` | Phase 1 setup guide: KoboldCpp + SDXL Turbo | `client/koboldcpp.py` `visual_automatic1111_setup`; the Visualizer's `automatic_setup` default staying `True`; a1111 backend config key names |
| `start-fork.bat` | One-shot launcher: Ollama → KoboldCpp → backend → frontend → Chrome | `src/talemate/server/run.py` CLI flags; `talemate_frontend` pnpm `serve` script; default ports 5050/8082 |
| `docs/fork/url-state-design.md` | Design + test results for URL state sync | — |
| `talemate_frontend/src/utils/urlState.js` | Hash ⇄ state, pure functions | — |
| `talemate_frontend/src/utils/urlStateSlices.js` | Reads/writes app state per URL slice | `scene_status.project_name` and `.save_files`; `WorldStateManager.tab` + `show()` + `$refs.characters.{selected,page}`; `AppConfig.{dialog,tab}` + `show()`; `DebugTools.{tab,selectTab}`; `VisualLibrary.{dialog,dialogModel,activeTab,sceneSelectedId,sceneInitialTab,sceneOpenNodes,open,openWithAsset}` and its `activeTab` values `review_queue`/`pending_queue`/`scene`; `VisualAssetsTree` folder-id format `VIS_TYPE::CharacterName`; `SceneMessages.{messages,viewedAssetId}` + `$refs.requestRegenerateInstructions`; `TalemateApp.availableTabs` |
| `talemate_frontend/src/components/UrlStateMixin.js` | Hash writer/reader + boot restore orchestration | `config.recent_scenes.scenes[].path`; `load_scene` / `request_scenes_list` / `scenes_list` message shapes; `registerMessageHandler` |
| `docs/fork/visual-consistency-design.md` | Design, amendments and verification results for image consistency | — |
| `src/talemate/agents/visual/anchors.py` | Derive-and-cache visual anchors; in-frame character matching | `Prompt.request` + `AnchorExtractor`; `set_processing` establishing the `ActiveAgent` context; the `visualize` prompt kind (150 tokens) |
| `src/talemate/prompts/templates/visual/derive-visual-anchor.jinja2` | Turns appearance/premise prose into fixed keyword lists | `character.base_attributes["appearance"]`; `scene.description` / `.context`; `llm_can_be_coerced()` |
| `tests/test_visual_anchor.py` | 43 tests: anchors, in-frame matching, sanitising, budget | — |
| `tests/test_visual_seed.py` | 10 tests: seed modes and the A1111 payload | — |

!!! warning
    `urlStateSlices.js` reaches into four upstream components through `$refs` and is
    the most likely thing here to be orphaned by an upstream refactor. It fails soft
    — every access is guarded, so a renamed field degrades that one URL slice rather
    than breaking the app — but a silent degradation is easy to miss. If a slice
    stops appearing in the hash after an upstream merge, start there.

!!! note
    `start-fork.bat` is a **new** file, deliberately not an edit to upstream's
    `start.bat` / `start-backend.bat` / `start-frontend.bat`. Those assume the
    embedded Python/Node that `install.bat` provisions, which this checkout does
    not use.

---

## Local-only, deliberately not committed

- `.git/info/exclude` — holds `.idea/`. Upstream's `.gitignore` doesn't cover it,
  and we don't want to modify a tracked file just for editor noise.
- `templates/world-state/fork-styles.yaml` — our visual-style group, currently the
  `Photoreal (Pony)` style that supplies the Pony `score_*` tags. A **new** file in
  that directory is genuinely gitignored; see the warning below before touching an
  existing one.
- `templates/llm-prompt/user/hf.co__TheDrummer__Rocinante-X-12B-v1-GGUF_Q4_K_M.jinja2`
  — copy of `std/Mistral.jinja2`, which is how Talemate pins a prompt template to a
  model. Without it the client silently falls back to `default.jinja2`.
- `.venv/Lib/site-packages/zzz_torchcodec_dll_fix.pth` — adds `torch/lib` and
  `.venv/Scripts` to the Windows DLL search path at interpreter startup. Without
  it `sentence_transformers` fails to import (torchcodec cannot resolve its
  dependencies), which breaks the **Memory agent** and therefore scene loading.
  Environment fix, not a code fix; **lost if `.venv` is recreated**. Recipe in
  `docs/fork/images-without-comfyui.md`. Root cause is `exclude-newer = "1 week"`
  in `pyproject.toml` resolving newer torch/torchcodec than upstream tested.
- FFmpeg 8 shared DLLs copied into `.venv/Scripts` (what `install-ffmpeg.bat`
  does). Also required by the above.

---

## Licence obligations (AGPL-3.0)

- `LICENSE` stays as-is, unmodified.
- Upstream attribution stays in `README.md`.
- Our modifications must be noted — this file is that record.
- Any new dependency must be AGPL-compatible. Check before adding.

---

## Known extension points that cost ZERO upstream diff

Prefer these. Established during recon (see `ARCHITECTURE.md` for detail):

| Want to add | Where | Mechanism |
|---|---|---|
| A new agent | `src/talemate/agents/custom/<name>/` | Auto-imported (`agents/custom/__init__.py:24-34`) |
| A new LLM client | `src/talemate/client/custom/` | Same pattern |
| Declarative node modules | `templates/modules/` | On `SEARCH_PATHS` (`nodes/__init__.py:10-19`) |
| An agent's own node modules | `<agent pkg>/modules/*.json` | `src/talemate/agents` is on `SEARCH_PATHS` |
| Prompt template overrides | `templates/prompts/<agent>/` | Ships `place-template-overrides-here.txt` |
| ComfyUI workflows | `templates/comfyui-workflows/` | Loaded by filename (`comfyui.py:37`) |
| Agent settings UI | nothing — declare `AgentAction`s | Rendered generically from `config_options()` |

## Known places that DO require a diff

| Want to add | Minimum diff | Note |
|---|---|---|
| A visual backend | 3 lines in `src/talemate/agents/visual/agent.py` | import + mixin base + `add_actions()` call. No plugin discovery for backends. |
| An HTTP asset route | new file in `src/talemate/server/` + 1 router include | Additive — the good kind of diff |

## Runtime dirties the working tree

Running the backend writes to **tracked** files, so `git status` is rarely clean
after using the app:

- `scenes/<scene>/assets/library.json` — every generated image is recorded here.
- `tests/data/scenes/talemate-laboratory/assets/library.json` — the startup asset
  migration touches this test fixture too (line endings at minimum).

Before committing, revert anything you didn't mean to keep:

```bash
git checkout -- scenes/ tests/
```

Worth remembering when reviewing a diff — image test-runs look like source changes.

!!! warning "Look at the diff before you revert it"
    That command is only safe for churn *you* just caused. `git checkout --`
    destroys working-tree changes irrecoverably, and a modification sitting in
    `scenes/` may predate your session — someone else's unsaved work. This bit us on
    2026-07-30: a pre-existing 154-line change to
    `scenes/infinity-quest-dynamic-story-v2/assets/library.json` was reverted as
    assumed noise, discarding the registrations for 12 locally generated images.
    `git diff -- scenes/` first, and only revert what you recognise.

    Recovery, for reference, was partial and lucky: the running backend still held
    the asset cache in memory (`_invalidate_cache` is never called), and a later
    write flushed most of it back. Upstream is no help — it ships only the 4 assets
    that came with the scene.

    The registry is not disposable in general: `library.json` is the only record of
    an asset's `vis_type`, `name` and prompt, and an unregistered PNG is invisible to
    the Visual Library even though the file is intact. Messages referencing an
    unregistered asset stop rendering it.

## Gitignore does not mean untracked

`.gitignore:23` lists `templates/world-state/*.yaml`, which reads as "all user data".
It isn't — these were committed by upstream *before* that rule was added, and
gitignore never untracks an existing file:

```
templates/world-state/visual-styles.yaml      # TRACKED - editing = upstream diff
templates/world-state/talemate/default.yaml   # TRACKED
templates/world-state/talemate/human.yaml     # TRACKED
```

**New** `.yaml` files in that directory *are* ignored, so add a new group file rather
than editing `visual-styles.yaml`. Always check first:

```bash
git ls-files templates/world-state/
git check-ignore -v <path>
```

The same caution applies anywhere a broad ignore pattern overlaps committed files.

## Do not touch

- `src/talemate/prompts/templates/conversation/dialogue.jinja2` — volatile-context
  ordering flips on a prompt-caching setting and upstream actively churns it.
  Use `DynamicInstruction` or a template override instead.
