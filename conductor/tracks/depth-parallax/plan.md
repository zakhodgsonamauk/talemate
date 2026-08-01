# Depth parallax — implementation plan

**Spec**: `conductor/tracks/depth-parallax/spec.md` (read fully — it carries the
licence obligation, the verified browser limits, and the card-crop trap).

Standing constraints: NEVER stop/start/restart the backend or Ollama without
the user's explicit go — coordinate restarts as a named step and wait.
ComfyUI read-only probes (`/object_info`, `/history`, `/system_stats`) are
always allowed; submitting a `/prompt` job is allowed for the lab since it
costs ~1-2s of GPU. Frontend dev server hot-reloads `.vue`. Commit per phase,
footer `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`. Featherweight
second-opinion review per phase; dispositions in `review-notes.md`.

Phase 0 output goes to the scratchpad, not the repo — generated imagery is
disposable. Only the harness code is committed.

---

## Phase 0 — Lab, then a decision gate

Goal: find out whether this is worth building, on our own art, before writing
any product code. Nothing in this phase ships.

**Phase 0 is COMPLETE — see `phase-0-findings.md`. Awaiting the T0.5 gate.**

### [x] T0.1 — depth workflow + extraction script

- `templates/comfyui-workflows/depth.json`: LoadImage →
  `DepthAnythingV2Preprocessor` → SaveImage. Title the nodes with the
  `Talemate ` prefix convention so the existing `Workflow` introspection can
  find them later (`Talemate Depth Source`, `Talemate Save Depth`).
- `scripts/depth_lab/make_depth.py`: take a scene path + asset ids (or
  `--all --limit N`), upload each asset via the ComfyUI `/upload/image`
  endpoint, submit `depth.json`, poll `/history`, write
  `<scratchpad>/depth_lab/<asset_id>.png` and `<asset_id>.depth.png`.
  Standalone script — do NOT wire it into the agent yet, phase 1 does that
  properly.
- Verify on 4+ real assets: ≥2 scene illustrations, ≥2 character cards.
  Confirm orientation and polarity of the depth output (near = white vs near =
  black) and record it — getting this backwards inverts the whole effect.

### [x] T0.2 — comparison lab page

- `scripts/depth_lab/lab.html` — single self-contained page, raw WebGL2, no
  build step, served by `python -m http.server` from the scratchpad dir.
- Two panes side by side over the same asset:
  - **A: naive** — single-sample UV displacement,
    `uv += camera.xy * (depth - focus) * amplitude`
  - **B: ray-marched** — the DepthFlow algorithm, two-stage forward probe then
    backward refine. Port it in this phase; phase 2 reuses the file.
    AGPL attribution header goes on from the first commit, not retrofitted.
- Live controls: amplitude, focus plane, depth height, march step cap, drift
  speed, and a static/animated toggle.
- FPS counter per pane.
- An aspect-ratio/crop toggle so cards can be viewed at their real 3:4 cover
  crop, not just letterboxed — the crop is where cards will actually break.

### [x] T0.3 — limits probe

- Same page, a "stress" mode that mounts N canvases and reports the N at which
  contexts start being lost. Run in Chrome and Firefox, record both numbers.
- Record frame rate at real display sizes (illustration ~350-700px wide, card
  ~150-250px) at several step caps, so phase 2 has a defensible default rather
  than a guess.

### [~] T0.4 — quality reference bar (optional, timeboxed 30 min) — SKIPPED

- `uvx depthflow` over the same 4 assets using our depth maps, output to
  scratchpad. Gives an upstream-quality video to compare our WebGL port
  against. If it fights the environment for more than half an hour, drop it —
  it is a nice-to-have, not a gate.

### [x] T0.5 — findings + STOP (written; gate open)

- `conductor/tracks/depth-parallax/phase-0-findings.md`: screenshots or clips,
  the naive-vs-raymarched verdict, the amplitude at which each visibly breaks,
  measured FPS and context ceilings per browser, chosen defaults.
- **HALT. Present to the user and wait for an explicit GO/NO-GO.**
  NO-GO → write the findings into `report.md`, mark the track closed at phase
  0, done. That is a legitimate ending.

**Commit (harness only). No review needed — nothing ships.**

---

## Phase 1 — Depth service (R1, R2, R3)

### [ ] T1.1 — sidecar helpers in scene_assets

- `depth_sidecar_path(asset_id)`, `has_depth(asset_id)`, `write_depth(bytes)`
  (PIL: convert `L`, **GaussianBlur radius 5**, save WebP q80),
  `read_depth_bytes(asset_id)`. The blur is not cosmetic - phase 0 showed it is
  what removes the silhouette streaks, and doing it here means zero runtime
  cost. Test that the written sidecar is measurably smoother than the input.
- Hook `remove_asset` to delete the sidecar.
- Carry the sidecar in `AssetTransfer` / scene export when present; absence is
  never an error.
- Tests in `tests/` for write/read/delete/absent. Do NOT touch `AssetMeta`,
  `Asset`, `library.json`, or `VIS_TYPE`.

### [ ] T1.2 — `ensure_depth` on the visual agent

- Loads `depth.json` through the existing `comfyui_load_workflow`, uploads the
  asset bytes with `Backend.upload_image`, submits, harvests with the existing
  `get_images` path.
- In-flight dedupe: `dict[asset_id, asyncio.Task]`, second caller awaits the
  first. Concurrency cap 2 via a semaphore.
- No-op when the sidecar exists. Never raises into the caller — a failed depth
  job logs and leaves the asset static.
- Tests: dedupe (10 concurrent → 1 job), no-op-when-present, failure is
  swallowed, cap respected.

### [ ] T1.3 — websocket action + delivery

- `visual` / `ensure_depth` action taking `asset_id`; replies with an
  `asset_depth` message carrying base64 + media type, mirroring the existing
  asset delivery shape.
- Frontend: extend the asset cache in `SceneMessages.vue` /
  `VisualAssetsMixin.js` with a `depthCache` keyed by asset id, plus a
  `requestAssetDepth` provide/inject sibling to `requestSceneAssets`.

**Commit + featherweight review.**

---

## Phase 2 — Renderer (R4)

### [ ] T2.1 — context pool module

- `talemate_frontend/src/components/parallax/contextPool.js`: N=2 WebGL2
  contexts (configurable), lease/release by element, LRU eviction, handles
  `webglcontextlost`/`restored` by falling the leaseholder back to static.
- Feature detect WebGL2 once; no WebGL2 → the pool reports unavailable and
  every consumer stays static.

### [ ] T2.2 — shader module

- `parallax/depthParallax.glsl.js` — lift the shader straight out of
  `scripts/depth_lab/lab.html` (FRAG_MARCH), which is already the cleaned-up
  port. AGPL attribution header travels with it. Steps default 64, plus the
  `uSoften` edge-soften uniform at 0.7 and a mip chain on the image texture -
  both proven free in phase 0.
- `parallax/renderer.js` — quad setup, texture upload (image + depth), RAF
  loop, Lissajous drift `(sin(0.31t), sin(0.23t))`, camera-offset uniform left
  exposed for future pointer input, pause on hidden tab.

### [ ] T2.3 — `ParallaxImage.vue`

- Props mirror what `MessageAssetImage` already has: src, depth, crop mode,
  amplitude, target aspect.
- Renders `<v-img>` until ALL of: depth present, pool lease acquired, motion
  setting on, `prefers-reduced-motion` not set. Then swaps to canvas.
- `IntersectionObserver` requests/releases the lease.
- Crop parity: lift `coverCrop()` from `scripts/depth_lab/lab.html` - phase 0
  verified it against a 768x1344 card at 250px (uvScale [1, 0.761], uvOffset
  [0, 0]) and the framing matches. Still test against a card first.

**Commit + featherweight review.**

---

## Phase 3 — Integration (R5)

### [ ] T3.1 — wire into MessageAssetImage

- `scene_illustration` and `card` asset types render `ParallaxImage`; `avatar`
  unchanged.
- On mount with no cached depth → fire `requestAssetDepth`, stay static, swap
  when it arrives.
- Amplitude per type from the phase-0 defaults.

### [ ] T3.2 — appearance setting

- `motion`: off | subtle | full, default subtle, next to the existing
  `display_size` in appearance settings. `subtle` and `full` scale the
  amplitude; `off` never creates a canvas.
- `prefers-reduced-motion` media query forces off and is re-evaluated live.

### [ ] T3.3 — click-through

- Verify the existing interactions survive the canvas swap: ctrl+click opens
  the asset view, shift+click regenerates, alt+click deletes-and-regenerates,
  plain click opens the asset menu (`MessageAssetImage.vue:191-222`).
  All of these currently hang off `<v-img>`.

**Commit + featherweight review.**

---

## Phase 4 — Verify + close

### [ ] T4.1 — verification + report

- pytest suites green, including the new depth-service tests.
- Live: AC2 (illustration swap, no layout shift), AC3 (card crop parity —
  screenshot diff before/after swap), AC4 (20+ message scroll in Chrome AND
  Firefox, no context loss), AC5 (reduced-motion and `motion: off` produce no
  canvas), AC6 (asset delete removes sidecar).
- `report.md`, `review-notes.md`, metadata, `tracks.md`, final review, push.

---

## DAG

```
T0.1 → T0.2 → T0.3 → T0.4? → T0.5 [GATE]
                                 ↓
                    ┌────────────┴────────────┐
                 T1.1 → T1.2 → T1.3      T2.1 → T2.2 → T2.3
                    └────────────┬────────────┘
                              T3.1 → T3.2 → T3.3
                                        ↓
                                      T4.1
```

Phase 1 and phase 2 are independent after the gate — the renderer can be built
against phase-0 depth files on disk while the service is written. They meet at
T3.1.

## Estimated complexity

**M** — 3.5-4.5 days after the gate, plus roughly half a day for phase 0.
Phase 0 can also end the track for the cost of that half day, which is the
point of it.
