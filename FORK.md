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
