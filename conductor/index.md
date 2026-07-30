# Project Status

**Last Updated**: 2026-07-30

## Current Focus

**url-state-sync** — the address bar now reflects app state (scene, tab,
world-editor path, drawers, major modals), restores on refresh, and back/forward
work. Hash-based, no vue-router, no new dependencies.

Covers scene, tab, world-editor path, drawers, app config, debug tools, the in-story
image viewer (`?img=`) and its editing prompt (`?edit=`, `&del=1`), and — third pass —
the Visual Library's own navigation: which of its three tabs is open, the selected
asset, that asset's info/reference/cover_crop sub-tab, and asset-tree expansion
(`?visual=`, `&vlopen=`).

Implemented and e2e tested: **all 25 cases pass** against the full local stack
(Ollama :11434, KoboldCpp :5001, backend :5050, frontend :8082), plus 55 ad-hoc
checks on the pure hash layer. Cost **6 upstream files, +93/-13**.

The insertion ceiling was dropped by explicit decision — it only ever estimated
future upstream-merge cost, and upstream sync is now treated as possible but
unlikely. `FORK.md` rows are still maintained as documentation.

Also fixed a latent bug in the shipped increment-1 code: blanket `%2C`/`%2F`
un-escaping in `stringify` would have torn a tree-folder name containing a comma
(`CHARACTER_PORTRAIT::Smith, John`) into two folder ids.

Design and per-case test results: `docs/fork/url-state-design.md`.
Step: EVALUATE_EXECUTION — awaiting review. Nothing committed yet.

## Recent Completions
(none)

## System Health
- Conductor v3 initialized
- Superpowers: enabled
- Local stack verified running: Ollama :11434, backend :5050, frontend :8082
