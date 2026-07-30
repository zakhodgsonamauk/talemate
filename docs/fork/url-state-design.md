# Design — URL reflects app state

Status: **implemented and e2e tested**, 2026-07-30. Track: `url-state-sync`.

Three things turned out differently from the design below; they are the parts worth
reading if you already know the plan:

1. **The writer is a 300ms poll, not a reactive watcher.** A computed reading child
   state through `$refs` registers no dependency, because `$refs` is not reactive
   and is empty until the child renders — and tabs render lazily here. This was
   caught in the browser: the world-editor tab changed and the hash didn't. See
   "Write path" below.
2. **The banner carries no timestamp.** `recent_scenes[].date` is stamped with the
   current time on every load (`config/schema.py:505-507`) — it is a last-opened
   marker, not a save time, and nothing in the payload records when a scene was
   actually saved. Asserting one would have been a fabrication.
3. **Only 2 upstream files were touched, not 6.** `WorldStateManager`, `AppConfig`
   and `DebugTools` declare no `expose` block, so the slice registry reads them
   with no change at all.

## Goal

The address bar should say where you are. Three payoffs, chosen deliberately:

1. **Debuggability** — the URL is a readout of app state, quotable in a bug report.
2. **Survive refresh / reconnect** — F5, or a backend restart, lands you back where
   you were.
3. **Back / forward work** — back closes a modal or returns to the previous tab
   instead of leaving the app.

**Explicitly out of scope: shareable / bookmarkable links into cold state.** Not a
preference — the backend accepts exactly one frontend websocket
(`src/talemate/server/api.py:37-52`), so a link opened in a second tab is rejected
with *"Another Talemate frontend is already connected."* Deep links work in the
same tab only. Designing for sharing would be designing for something the backend
forbids.

## Agreed scope

| Decision | Choice |
|---|---|
| State in URL | Tab + scene + world-editor deep path + drawers + major modals |
| URL shape | Hash (`#/...`) |
| Restore on load | Auto-load the scene, with a banner naming it and its save time |
| Not doing | All 58 `v-dialog` components; real paths; vue-router; cross-tab |

## What the codebase actually looks like

Established by reading it, not assumed:

- **There is no router.** `talemate_frontend/package.json` has no `vue-router`.
  `App.vue` is a 15-line shell wrapping `TalemateApp.vue` (1742 lines), which holds
  every piece of top-level navigation state as component data.
- **Tabs**: `tab` ∈ `home | main | world | package_manager | templates | prompts`
  (`TalemateApp.vue:457-526`).
- **Drawers**: `drawer`, `sceneDrawer`, `debugDrawer`, `directorConsoleDrawer`
  (`TalemateApp.vue:531-534`).
- **Modals** are opened imperatively through `$refs` in the `provide()` block
  (`TalemateApp.vue:798-864`) — e.g. `visualLibrary.openWithAsset(assetId, tab)`
  (`:830`), `debugTools.selectTab(tabValue)` (`:851`). 58 files contain a
  `v-dialog`.
- **The only existing `window.location` use in the whole frontend** is
  `TalemateApp.vue:920`, deriving the websocket host. Nothing else reads or writes
  the URL.
- **A 4-level navigation event already exists**:
  `world-state-manager-navigate(tab, sub1, sub2, sub3)`, threaded through
  `WorldStateManagerMenu.vue:8-48`. It maps 1:1 onto a URL path — the world-editor
  half of this feature is mostly wiring, not new design.

### Refresh destroys server-side scene state

This is the fact the whole restore design turns on.

When the socket closes, `frontend_disconnect` (`api.py:66-86`) sets
`scene.active = False`, `continue_scene = False`, and cancels `scene_task`. The
reconnecting browser gets a **brand new** `WebsocketHandler` whose constructor does
`self.scene = Scene()` (`websocket_server.py:53`) — an empty scene. Nothing is
retained across a refresh.

Consequences:

- Restoring a scene means **re-issuing `load_scene`**, i.e. reloading from disk.
- In-memory progress is already gone before the new socket exists, so auto-reload
  destroys nothing that the refresh didn't. But it does change refresh from *inert*
  to *acts on its own* — hence the banner.
- `auto_save` defaults to `True` (`config/schema.py:201`, surfaced at
  `AppConfig.vue:56` as `appConfig.game.general.auto_save`) and saves after each
  game loop (`nodes/scene.py:1707`), so disk normally tracks live state closely.
  With auto-save **off**, restore shows silently stale content — the banner must
  warn in that case.
- `api.py:38` rejects the new socket if the old one hasn't finished closing. The
  frontend already retries every 3s (`TalemateApp.vue:548`), so restore must be
  retry-tolerant, not fire-once.

### Scene identity is already URL-safe

No need to put filesystem paths in the URL:

- `Scene.project_name` (`tale_mate.py:311-314`) is `name.lower()` with spaces
  hyphenated and apostrophes stripped — already a slug, and already shipped to the
  frontend in the `scene_status` payload (`tale_mate.py:1382`).
- `save_dir` is `scenes_dir()/project_name` (`:354`), `full_path` is
  `save_dir/filename` (`:366`). So slug + filename fully determine the path.
- Slug → path resolution needs no backend change: `config.recent_scenes.scenes`
  (`config/schema.py:682`) carries `path`, `name`, `filename` per entry, and
  `app_config` is already requested on connect (`TalemateApp.vue:946`). The lookup
  table arrives exactly when restore needs it.
- `project_name` has a setter (`:317`) that can override the derived value, so
  always read `data.project_name` off the payload. **Never re-derive the slug
  client-side.**

## URL grammar

Path is *where you are* (one location). Query is *what's overlaid* (orthogonal,
several can be open at once).

```
#/                                              home, nothing loaded
#/templates                                     global tab
#/prompts?p=llm-templates                       global tab + sub-tab
#/s/<project_name>                              scene loaded, implies main
#/s/<project_name>/main
#/s/<project_name>/world/<page>[/<sub1>[/<sub2>]]
#/s/<project_name>/mods
```

Overlays, all optional:

```
?visual=<assetId>          visual library open on an asset
&config=<page>             app config open on a page
&debug=<tab>               debug tools open on a tab
&d=scene,director          open drawers, comma-separated
&save=<filename>           only when not the scene's default save file
&img=<assetId>             story image open in the full-size viewer
&edit=<assetId>            image-editing instructions prompt for a story image
&del=1                     that edit discards the original ("Edit and Delete")
```

`img` and `edit` are distinct from `visual`: the first two are the in-story image
viewer and its edit prompt, which live in `SceneMessages` and only exist on the main
tab; `visual` is the separate Visual Library dialog. All three can be open at once,
which is why they are separate keys rather than one.

`del` is never emitted without `edit` — on its own it would describe nothing.

Examples:

```
#/s/infinity-quest/world/characters/Kaira/attributes
#/s/infinity-quest/main?visual=asset-3f9c1a&d=scene
#/s/infinity-quest/world/history?debug=prompts
```

Character names need `encodeURIComponent` — they contain spaces and apostrophes.

## Architecture

Three new files, no new dependencies, and no upstream file gains routing logic.

1. **`src/utils/urlState.js`** — pure functions `parse(hash) -> State` and
   `stringify(State) -> hash`. No Vue import. Round-trip is unit-testable in
   isolation.
2. **`src/components/UrlStateMixin.js`** — the listener/writer lifecycle. Matches
   the existing mixin convention (`AssetViewMixin.js`, `MessageAssetMixin.js`,
   `VisualAssetsMixin.js`) rather than introducing composables into an
   Options-API codebase.
3. **`src/utils/urlStateSlices.js`** — the registry. Each slice declares its key,
   a `read()` and a `write(v)`. Keeps per-component knowledge out of
   `TalemateApp.vue`, so adding a modal later is a registry entry, not a new diff
   in the shell.

### Why not vue-router

There are no route components to route to. `App.vue` is a 15-line shell; adopting
vue-router means restructuring it around `<router-view>`, adding a dependency, and
inheriting machinery for problems we don't have (code splitting, guards, named
views). `hashchange` plus a debounced writer is ~120 lines and touches nothing
upstream. Revisit only if we later want real paths or lazily-loaded views.

### Write path (state → URL)

**As built: a 300ms interval** that compares `stringify(readState(app))` against the
current hash and writes only on a difference. Skipped while `restoring` is true.

The original design — a `watch` on a computed aggregate — does not work here, and
the reason generalises. The state being mirrored lives in child components reached
through `$refs`: the world-editor page, the selected character, the open modal.
`$refs` is not reactive and is empty until the child renders. A computed that reads
it therefore registers no dependency on the child's data, and never re-runs when
that data changes. Because the tabs render lazily, this is the normal case rather
than an edge case. Observed directly: clicking the world-editor Characters tab left
the hash on `/world/scene`.

A poll costs a handful of property reads and a string compare, exits early when the
hash already matches, and is immune to ref timing. Push vs replace is decided from
the diff, not by the caller: a change of location (tab, world-editor page, a modal)
gets `pushState` so `back` undoes it, while a drawer toggle gets `replaceState` so
sidebars don't fill the history stack.

Note that `pushState`/`replaceState` do not fire `hashchange`, so the writer cannot
feed the reader at all. The `_lastWritten` guard remains as a belt-and-braces for
the `location.hash =` fallback path, which does fire.

### Read path (URL → state)

Two triggers only:

1. **Boot restore** — runs after `scene.loaded`, never on `ws.onopen`.
2. **User navigation** — a `hashchange` whose hash is not `_lastWritten`.

### Boot restore sequence

```
page load
  snapshot location.hash into bootTarget      <-- in created(), before connect
  no scene in bootTarget?  apply view state now, done

  connect ws (existing 3s retry loop, TalemateApp.vue:548)
    rejected "already connected" -> keep retrying, do NOT clear the URL

  on open   -> requestAppConfig()             <-- already happens, :946
  on app_config
    resolve bootTarget.scene against config.recent_scenes.scenes
      hit  -> send load_scene { file_path: entry.path }
      miss -> send request_scenes_list { query: slug }
      still miss -> banner "couldn't find <slug>", land on home

  on scene_status started
    assert data.project_name === bootTarget.scene    <-- guard wrong-scene restore
    apply tab
      $nextTick -> apply world-editor path
        -> apply drawers
          -> apply overlays
    banner: "Reloaded <title> (saved <mtime>)"
    if !appConfig.game.general.auto_save -> warning variant: content may be behind
```

Two traps this ordering exists to avoid:

- **`availableTabs` watcher** (`TalemateApp.vue:592-601`) force-resets `tab` to
  `home` whenever the target tab's `condition()` is false, and `main`, `world` and
  `mods` all gate on `sceneActive`. Applying the tab before the scene lands gets
  you silently bounced to home. This is the single most likely bug in the feature.
- **`onclose` sets `sceneActive = false; scene = {}`** (`:954-955`), tripping that
  same watcher, which resets `tab`, which fires the URL *writer* — clobbering the
  hash with `#/` before restore ever reads it. Hence snapshotting the boot hash in
  `created()` and never re-reading `location.hash` afterwards.

## Upstream diff — as built

| File | Lines | What |
|---|---|---|
| `TalemateApp.vue` | +16 / -1 | mixin import + registration, restore banner markup |
| `VisualLibrary.vue` | +5 / -1 | three names added to its `expose` list, with a comment |
| `SceneMessages.vue` | +10 / -0 | central `viewedAssetId` + its two provides (story image viewer) |
| `MessageAssetImage.vue` | +33 / -9 | `showAssetView` becomes a computed over the provided central state; `inject` switched to object form so the two new keys can default to null |
| **New files** | 861 | `urlState.js` 220, `urlStateSlices.js` 228, `UrlStateMixin.js` 413 — zero conflict risk |

**4 upstream files, 64 insertions, 11 deletions** — against a budget of 6 files and
~70 lines. Note the insertion count is now close to that ceiling; a further slice of
this size would need the budget revisited rather than quietly exceeded.

### Why the story image viewer needed a component change

`MessageAssetImage` renders one `AssetView` **per message** and each instance held
its own `showAssetView` flag. That makes "which image is open?" unanswerable from
any single place, which URL sync needs in both directions — to read for the hash,
and to set when restoring.

So `SceneMessages` now owns `viewedAssetId` and provides a getter/setter; each
`MessageAssetImage` derives its dialog from it (`showAssetView` is a computed whose
setter writes the central id). The markup stays where upstream put it, so the
conflict surface is a few lines rather than a moved component.

A side effect worth knowing: only one story image viewer can now be open at a time.
That was already true in practice — the dialogs are modal — but it is now enforced
by construction.

The injects are declared in object form with `default: null` so that if
`MessageAssetImage` is ever rendered outside `SceneMessages`' provide scope it falls
back to a local flag instead of throwing.

It stayed low because `WorldStateManager.vue`, `WorldStateManagerMenu.vue`,
`AppConfig.vue`, `DebugTools.vue` and `SceneMessages.vue` declare no `expose` block,
so their reactive data and their existing `show()` / `selectTab()` / `openDialog()`
methods are reachable through `$refs` with **no change for the read/apply side**.
`VisualLibrary.vue` is the sole exception: it does declare `expose`, so `dialog`,
`sceneSelectedId` and `dialogModel` had to be added to that list.

The image-editing prompt cost **zero** component changes for the same reason:
`SceneMessages.$refs.requestRegenerateInstructions` is a `RequestInput` whose `open`
flag and `extra_params` are readable, and whose `openDialog()` restores it.

`dialogModel` rather than `dialog` matters — its setter runs the unsaved-changes
confirmation (`VisualLibrary.vue:353-372`), so closing the library from a back-press
prompts instead of silently discarding edits.

`FORK.md` goes from 1 modified-file row to 3.

### On the word "rewrite"

The original ask was to rewrite the front-end. This design deliberately doesn't.
A real rewrite — vue-router plus route components across 190 components — is
10-50× the diff for identical outcomes on all three stated goals, and runs directly
against `BRIEF.md:34-38` ("every line we change in an existing file is a future
merge conflict"). Recorded here in case the rewrite is wanted for reasons beyond
URL state.

## Risks

1. **`availableTabs` reset race** — highest. Mitigated by the ordering above.
   *Verified:* a clean load of `#/s/<slug>/world/characters` restores World Editor →
   Characters, so the tab is not bounced to home.
2. **Reconnect storm.** Backend restart → repeated reconnects → repeated
   `load_scene`. Guard with an `autoLoadDone` flag: auto-load at most once per page
   load; later reconnects restore view state only. *Verified:* dropping the socket
   mid-session produced **0** further `load_scene` sends.
3. **Stale content with auto-save off.** Handled by the banner warning, not by code.
   *Verified:* with auto-save off the warning variant fires, does not auto-dismiss,
   and says plainly that anything after the last manual save is missing.
4. **Two save files in one scene dir** need `&save=<filename>` to disambiguate.
   Slug collisions across *directories* are impossible, since the slug *is* the
   directory name.
5. **No JS test infrastructure.** `package.json` has no test script or runner. The
   pure `urlState.js` round-trip deserves unit tests; adding vitest is a separate
   decision, not smuggled into this track.

## Known limitations

- **The visual library only appears in the URL when an asset is selected.** `?visual=`
  carries an asset id; opening the library from the toolbar without picking an asset
  records nothing, so that state does not survive a refresh. Fixing it means a
  separate "open, no selection" marker; not done, because the useful deep link is to
  an asset. Reaching it via the asset menu's "Open in Visual Library" always selects
  one, so that path is covered.
- **A hash change never loads a scene.** Editing the slug in the address bar, or
  reaching a different slug via back/forward, applies only view state — the loaded
  scene is left alone and the writer then corrects the hash back. Loading tears down
  the previous scene server-side (`websocket_server.py:155-166`), which is too
  destructive for a back-press. Scene loading happens only on a fresh page load.
- **Only one story image viewer can be open at a time**, now enforced by the single
  `viewedAssetId`. Modal dialogs made this true in practice already.
- **`?img=` and `?edit=` are ignored when the named asset is not referenced by any
  loaded message.** A stale id shows nothing rather than an empty dialog. Note the
  message list is windowed by `max_backscroll`, so an id from far enough back in a
  long scene may not resolve.
- **`urlState.js` has no committed tests.** Its parse/stringify round trip is pure
  and was verified with a 38-case ad-hoc script during development, but
  `talemate_frontend/package.json` has no test runner, so there is nothing to commit
  it into. Adding vitest is a separate decision.

## Test plan

Manual — needs a live backend, and per `BRIEF.md:41` anything requiring a running
model gets handed over to test.

Run against a live backend on 2026-07-30 via Playwright (backend :5050, frontend
:8082, scene `infinity-quest-dynamic-story-v2`). Cases 3, 7 and 14 were re-run after
restarting the stack with images enabled (KoboldCpp on :5001).
**All 14 passed.**

A measurement caveat, since it produced one false negative during testing: the
restore banner auto-dismisses after 6s and clears `urlRestoreNotice` on close, so
sampling the DOM more than ~6s after the scene lands finds nothing. That is the
banner working, not failing. Timed runs put both the banner and the restored overlay
in place together.

1. **PASS** — Load a scene, go to world editor → characters → *Kaira* → attributes,
   reload. Lands back on `/world/characters/Kaira/attributes`; banner appears naming
   the scene. (No save time, by the decision at the top of this file.)
2. **PASS** — Back from there steps `attributes → description`, in both the hash and
   the rendered tab. Back from a tab change steps tabs; back from an open modal
   closes it. Never left the app.
3. **PASS** — The asset menu's "Open in Visual Library" writes
   `&visual=<assetId>`, and a cold load of that link reopens the Visual Library on
   that asset (verified with the full stack up, `AUTOMATIC1111` backend reported in
   the dialog header).
4. **PASS (adapted)** — Tested by force-closing the websocket mid-session rather
   than restarting the backend, which exercises the same reconnect path.
   Result: **0** `load_scene` sends after the drop, reconnect succeeded, hash
   collapsed to `#/` to reflect that no scene is loaded. Note the design intent:
   on reconnect the scene is deliberately **not** re-loaded, only view state is.
5. **PASS** — `#/s/no-such-scene-xyz/world/characters` →
   *"No scene named "no-such-scene-xyz" was found — showing the home screen
   instead."*, hash rewritten to `#/`, home tab, no crash, no retry loop.
6. **PASS** — Second tab gets *"Another Talemate frontend is already connected"*,
   unchanged. Closing the holder let the other tab connect on its next 3s retry.
7. **PASS** — With `game.general.auto_save` turned off via the app config dialog,
   a cold load produced the warning variant verbatim: *"Reloaded Infinity Quest from
   disk. Auto-save is off, so anything after the last manual save is not here."*
   It also stays put — still visible after 9s, unlike the 6s info variant — and
   carries a DISMISS button. The setting was turned back on afterwards and
   `config.yaml:983` confirmed back at `auto_save: true`.
8. **PASS** — Setting the hash to a *different* scene slug while one is loaded:
   0 `load_scene` sends, the live scene untouched, view state applied, and the
   writer corrected the hash back to the true slug. No oscillation.

### Story image viewer and edit prompt (added after the first pass)

9. **PASS** — Ctrl+click a story image → `&img=<assetId>`. Back closes the viewer.
   Escape closes it too, and the hash drops `img=` (the component's own close path
   feeds the URL, not just our own close call).
10. **PASS** — Clean load of `…/main?save=…&img=<assetId>` → scene reloads, then the
    full-size viewer opens on that asset.
11. **PASS** — The asset menu's own "View Image" item also produces `&img=`, so both
    entry points into the viewer are covered.
12. **PASS** — Clean load of `…&edit=<assetId>&del=1` → the "Image Editing
    Instructions" prompt opens for that asset, and the hash keeps both keys.
    Retaining them across many 300ms poll cycles is what proves the *read* side:
    if `readState` weren't seeing the prompt, the writer would have stripped
    `edit`/`del` within a third of a second.
13. **PASS** — A hash change back to the plain scene URL closes the edit prompt.
14. **PASS** — Re-run with the full stack (KoboldCpp on :5001, so
    `visual.meta.image_create.status == OK` and `visualAgentReady` true, which
    un-disables the menu items). Clicking **Edit Illustration** writes
    `&edit=<assetId>` with no `del`; clicking **Edit and Delete** writes
    `&edit=<assetId>&del=1`. The two variants are distinguished correctly.

    Note `imageEditAvailable` is still false — `backend_image_edit` is empty in
    `config.yaml` — but the menu gates on `visualAgentReady`, not on that, so this
    exercises the real code path regardless.

Also verified incidentally, since each was a prerequisite: tab → hash
(`#/templates`), scene load → hash (`#/s/<slug>/main?save=...`, with `save=`
appearing only because that scene has multiple save files), world-editor page →
hash, character and character-page → hash, drawer toggle → `?d=scene`, app config →
`?config=game`, debug drawer and its tab → `?debug=memory_requests`.

## Files this adds

For the `FORK.md` "Files we added" table once implemented:

| Path | Purpose | Depends on (upstream contract) |
|---|---|---|
| `talemate_frontend/src/utils/urlState.js` | Hash ⇄ state, pure | — |
| `talemate_frontend/src/components/UrlStateMixin.js` | Listener/writer lifecycle | `TalemateApp` data shape |
| `talemate_frontend/src/utils/urlStateSlices.js` | State slice registry | `scene_status.project_name`; `config.recent_scenes.scenes[].path`; `world-state-manager-navigate` arity |
