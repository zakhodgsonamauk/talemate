# Track: url-state-sync

**Type**: ui
**Created**: 2026-07-30
**Design doc**: `docs/fork/url-state-design.md` (read it — this spec does not repeat it)

## Problem

The frontend has no router. All navigation state lives as component data in
`TalemateApp.vue`. The address bar never changes, so:

- you can't tell from the URL what the app is showing (bug reports lose context)
- refresh drops you on `home` with nothing loaded
- the back button leaves the app

## Goal

The hash reflects location and overlays. Refresh and reconnect restore it. Back and
forward move through app state.

Out of scope, and not by preference: **shareable links into cold state**. The
backend accepts one frontend websocket (`src/talemate/server/api.py:37-52`), so a
link opened in a second tab is rejected. Same-tab only.

## Acceptance criteria

1. **AC1 — Hash mirrors state.** Changing tab, world-editor page, world-editor
   character, drawer, or a major modal updates the hash within ~200ms. Grammar
   matches the design doc:
   `#/s/<project_name>/world/<page>/<sub1>/<sub2>` plus
   `?visual= &config= &debug= &d= &save=`.
2. **AC2 — Refresh restores.** With `#/s/<slug>/world/characters/<name>/attributes`
   in the bar, F5 re-loads the scene and lands on that character's attributes.
3. **AC3 — Banner on auto-load.** Restore shows a dismissible banner naming the
   scene. If `appConfig.game.general.auto_save` is false, the banner uses a warning
   variant stating content may be behind.

   *Amended during implementation:* originally "naming the scene **and its save
   time**". There is no save time to name. The only timestamp reachable from the
   frontend is `recent_scenes[].date`, which `RecentScenes.push()` sets to the
   current time on every load (`config/schema.py:505-507`) — a last-opened marker.
   Surfacing it as a save time would have been a fabrication, so the banner states
   only that the scene was reloaded from disk. Getting a real save time would need a
   backend change (file mtime in the payload) and is out of scope.
4. **AC4 — Back/forward.** Back steps through hash history — closes an open modal,
   or returns to the previous tab — without leaving the app.
5. **AC5 — Auto-load happens at most once per page load.** A backend restart
   mid-session reconnects and restores *view* state, but must not re-issue
   `load_scene` on every reconnect.
6. **AC6 — Bad slug degrades.** A hand-edited hash naming a nonexistent scene shows
   an error banner and lands on `home`. No crash, no infinite retry.
7. **AC7 — No echo loop.** Writer-initiated hash changes do not re-trigger restore.
   Deep-linking to the state the app is already in causes no flicker and no second
   `load_scene`.
8. **AC8 — Second tab unchanged.** The one-websocket rejection still behaves as
   before; closing the first tab lets the second recover.
9. **AC9 — Diff budget respected.** At most 6 upstream files modified, ~70 lines
   total. Routing logic lives in the 3 new files. No new npm dependency.

## Non-goals

- vue-router
- real (non-hash) paths, and therefore no `frontend_wsgi.py` change
- URL identity for all 58 `v-dialog` components
- adding a JS test runner (flagged as a separate decision)

## Known hazards (from investigation — do not rediscover)

- `TalemateApp.vue:592-601` `availableTabs` watcher force-resets `tab` to `home`
  when the target tab's `condition()` is false. `main`/`world`/`mods` gate on
  `sceneActive`. **Restoring the tab before `scene_status` arrives silently bounces
  to home.**
- `TalemateApp.vue:954-955` `onclose` sets `sceneActive = false; scene = {}`,
  tripping that watcher, resetting `tab`, firing the writer, clobbering the hash.
  **Snapshot the boot hash in `created()`; never re-read `location.hash` for
  restore.**
- `api.py:38` rejects the reconnecting socket while the old one is closing. Restore
  must tolerate the existing 3s retry loop (`TalemateApp.vue:548`).
- `Scene.project_name` has a setter (`tale_mate.py:317`) that can override the
  derived slug. **Read `data.project_name` off the payload; never re-derive it.**
- Character names contain spaces and apostrophes — `encodeURIComponent` required.
