# Plan: url-state-sync

Design: `docs/fork/url-state-design.md`. Spec: `spec.md`.

## DAG

```
T1 urlState.js (pure)
  |
  +--> T2 urlStateSlices.js registry
  |      |
  |      +--> T4 component slice wiring (T5..T8 independent of each other)
  |
  +--> T3 UrlStateMixin.js
         |
         +--> T9 TalemateApp wiring   (needs T2, T3, T4)
                |
                +--> T10 e2e
                       |
                       +--> T11 FORK.md
```

## Tasks

### T1 — `talemate_frontend/src/utils/urlState.js` (new)
`parse(hash) -> State`, `stringify(State) -> hash`. Pure, no Vue.
`State = { scene, save, tab, wsm: [page, sub1, sub2], overlays: {visual, config, debug}, drawers: [] }`.
Unknown keys are preserved on parse and re-emitted on stringify, so a future slice
can't be silently eaten. `encodeURIComponent` every path segment.
Deps: none.

### T2 — `talemate_frontend/src/utils/urlStateSlices.js` (new)
Slice registry: `{ key, read(app), write(app, value) }` per slice. Adding a modal
later = one entry here, not a new diff in the shell.
Deps: T1.

### T3 — `talemate_frontend/src/components/UrlStateMixin.js` (new)
- `created()`: snapshot `location.hash` into `this._bootHash` **before** connect.
- debounced (150ms) writer, skipped while `this._urlRestoring`.
- `_lastWritten` guard so writer-initiated `hashchange` is ignored (AC7).
- `hashchange` listener → `applyUrlState()` for back/forward (AC4).
- teardown in `beforeUnmount`.
Deps: T1.

### T4 — component slices

| # | File | Work |
|---|---|---|
| T5 | `WorldStateManagerMenu.vue` | expose `currentNav()` returning `[tab, character]` |
| T6 | `WorldStateManager.vue` | expose `currentNav()`; accept `applyNav(page, sub1, sub2)` |
| T7 | `VisualLibrary.vue` | expose `currentAssetId()`; emit close so writer clears `?visual` |
| T8 | `AppConfig.vue` / `DebugTools.vue` | expose current page / tab; accept apply |

`openWithAsset` (`TalemateApp.vue:830`) and `selectTab` (`:851`) already exist —
reuse, don't reinvent.
Deps: T2.

### T9 — `TalemateApp.vue` wiring
- register mixin; aggregate computed `urlState` feeding the writer
- restore orchestration gated on `scene_status`, ordered
  `tab → $nextTick → wsm → drawers → overlays` (AC2; avoids the `availableTabs`
  reset hazard)
- `_urlAutoLoadDone` flag: auto-load at most once per page load (AC5)
- slug → path via `config.recent_scenes.scenes`, fallback `request_scenes_list`,
  then error banner (AC6)
- reload banner with save time + `auto_save`-off warning variant (AC3)
Deps: T2, T3, T4.

### T10 — e2e
`SKIP_IMAGES=1 NO_BROWSER=1 start-fork.bat`, then the 8 cases in the design doc's
test plan, driven through Playwright.
Deps: T9.

### T11 — `FORK.md`
Add modified-upstream rows (with upstream line numbers at time of change) and the
3 new-file rows.
Deps: T10.

## Diff budget

Ceiling from AC9: 6 upstream files, ~70 lines. Exceeding it is a stop-and-report,
not a silent overrun.
