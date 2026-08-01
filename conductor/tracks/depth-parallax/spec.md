# Depth parallax — specification

## Goal

Scene illustrations and character cards currently sit dead on the page. Give
them subtle, continuous 2.5D camera motion driven by a per-asset depth map, so
the image the reader is looking at breathes without any of it being video.

No diffusion, no video files, no GPU cost after a one-off depth pass per asset
(measured at ~10.7s in phase 0, see `phase-0-findings.md`). Motion is rendered
client-side in WebGL2 from `image + depthmap`.

Investigation that produced this track (2026-08-01, this repo's ComfyUI at
localhost:8188, RTX 4080 16GB, `--reserve-vram 5`):

- `DepthAnythingV2Preprocessor` is ALREADY installed (controlnet_aux), along
  with Zoe/MiDaS/Metric3D. No new custom nodes needed.
- The whole WAN 2.2 I2V + Lightning stack is also installed — real video was
  costed at 2-3 min GPU per clip and rejected for the default experience. It
  stays available as a future opt-in track; nothing here forecloses it.
- No npm package does depth-map displacement (every Vue/React "parallax"
  package is layer-stacking or tilt). `talemate_frontend/package.json` has no
  three.js / pixi / regl. This is greenfield, written against raw WebGL2.
- Prior art to port from: **BrokenSource/DepthFlow**, AGPL-3.0 (same licence
  as Talemate), v1.0.0 released 2026-06-15, 1.5k stars. Single ~180-line
  shader at `depthflow/resources/depthflow.glsl`, ray-marched, claims
  artifact-free edges and seamless loops.

## Requirements

### R0 — Test harness first, decision gate before any product code

Phase 0 is a throwaway-quality but repo-committed lab that answers three
questions on THIS project's actual art, before a line of Vue is written:

1. Does the effect look good on our illustrations and 3:4 character cards, or
   does it read as cheap?
2. Naive single-sample UV displacement vs ported ray-marching — is the extra
   shader complexity worth it, and at what amplitude does each break?
3. What are the real client-side limits — frame rate at our display sizes, and
   the WebGL context ceiling.

Phase 0 ends with an explicit STOP and a user GO/NO-GO. A NO-GO closes the
track at phase 0 with the findings written up; that is a successful outcome,
not a failure.

### R1 — Depth generation (backend)

- New standalone workflow `templates/comfyui-workflows/depth.json`:
  LoadImage → `DepthAnythingV2Preprocessor` → SaveImage.
  Standalone deliberately, NOT a branch bolted into the 8 existing workflows:
  it works retroactively on assets already in the libraries, avoids editing
  every workflow, and sidesteps a real bug —
  `comfyui.py:653-663` `get_images` overwrites `response.generated` per output
  node, so a second SaveImage in an existing workflow would hand back whichever
  node ComfyUI happened to order last.
- `VisualAgent.ensure_depth(asset_id)`:
  - no-op when the sidecar already exists
  - dedupes in-flight requests per asset id (a scene load fires many at once)
  - caps concurrency (2)
  - uploads the asset via the existing `Backend.upload_image`, runs the
    workflow through the existing `/prompt` + `get_images` path
  - writes the result as a grayscale WebP sidecar
- Depth jobs queue behind any running illustration in ComfyUI. That is fine —
  nothing blocks on them. They must never stall the UI or the visual queue.

### R2 — Sidecar storage (deliberately NOT an asset)

- File: `<asset_dir>/<asset_id>.depth.webp`, grayscale, quality 80.
  Expected ~80-150KB at 1216x832.
- **The writer blurs the depth map by ~5px before saving** (PIL
  `GaussianBlur(5)`). Phase 0 proved this is what removes the trailing
  silhouette streaks that ray marching otherwise leaves at a depth cliff:
  softening the discontinuity turns a hard streak into a gentle stretch. Baked
  in at generation time, so it costs nothing at runtime and every client gets
  it. This is the highest-value line of code in the track.
- NOT registered in `library.json`, NOT a new `VIS_TYPE`, no change to
  `AssetMeta` (`scene_assets.py:275`) or `Asset` (`scene_assets.py:313`).
  A depth map is not a picture the user owns — making it an asset means it
  shows in the Visual Library grid, in search results, in auto-attach, and in
  `Image.open()` format re-derivation, all of which then need suppressing.
- Deleting an asset deletes its sidecar.
- Scene export/import and `AssetTransfer` carry the sidecar if present; a
  missing sidecar is never an error, it just means static render until
  regenerated.

### R3 — Delivery

- New websocket request `visual` / `ensure_depth` with `asset_id`.
- Response reuses the existing base64 asset delivery shape, distinct message
  type (`asset_depth`), cached client-side alongside the image cache used by
  `MessageAssetImage.vue:121-133`.
- No HTTP asset endpoint. That was required only for the video route and is
  explicitly out of scope here.

### R4 — Renderer (frontend)

- New `ParallaxImage.vue` + a WebGL2 context pool module.
- Shader ported from DepthFlow's `depthflow.glsl`. **AGPL-3.0 attribution
  header is mandatory** on the ported file — name the project, the author
  (Tremeschin), the licence, and the upstream path. Talemate is AGPL-3.0 so
  the licences are compatible; the attribution is not optional.
  Port work is: strip the ShaderFlow uniform structs (`iCamera`, `iDepth*`)
  and helpers (`CameraProject`, `gtexture`), rewire for GLSL ES 3.00.
- Motion driver: slow auto-drift loop. Lissajous `(sin(0.31t), sin(0.23t))`,
  ~10s perceived cycle, no visible repeat. No pointer tracking in v1 (dead on
  touch, only active on hover) — the shader keeps a camera-offset uniform so
  pointer influence can be added later without touching the pipeline.
- **Context pooling is mandatory, not an optimisation.** Verified limits:
  Chrome 16 desktop / 8 Android; Firefox 8 per principal and 2 on mobile.
  A scrolled scene has dozens of illustrated messages. Pool 1-2 contexts,
  handed by `IntersectionObserver` to whatever is on screen; everything else
  renders the existing static `<v-img>`. Escape hatch if the pool proves too
  restrictive: `greggman/virtual-webgl`.
- **Edge soften**: the shader measures how violently depth changed across the
  hit step (`|h - hPrev| / dz`) and, where that exceeds a smooth slope, samples
  a coarser mip. Residual streaking becomes a soft smudge that reads as
  depth-of-field. Requires a mip chain on the image texture. Measured free in
  phase 0. Default 0.7.
- Cost control: ray-march steps 64 (phase-0 measured: 0.063 ms for a
  700x400 illustration on a 4080; linear in step count, 128 is affordable),
  force devicePixelRatio 1, pause the RAF loop when the tab is hidden or the
  element leaves the viewport.
- Graceful degradation at every step: no WebGL2, no depth sidecar yet, context
  lost, `prefers-reduced-motion` — all fall back to today's static `<v-img>`
  with no layout shift.

### R5 — Integration and scope

- Applies to **scene illustrations** and **character cards** only. Not avatars
  (56-112px, motion invisible, wastes a context slot). Not scene cover in v1.
- Character cards carry a crop: `MessageAssetImage.vue:289` forces
  `object-position: top` and `AssetMeta.cover_bbox` drives framing elsewhere.
  The canvas path must reproduce that crop exactly or a card visibly jumps the
  moment its depth map arrives. This is the fiddliest part of the track.
- Appearance setting `motion`: `off` | `subtle` | `full`, sitting alongside the
  existing `display_size`. Default `subtle`. `prefers-reduced-motion` forces
  `off` regardless of the setting.
- Amplitude per type, phase-0 tuned: **illustrations 3%, cards 2%**. With the
  depth blur and edge soften in place these are clean, and 8% is usable if a
  stronger effect is ever wanted — amplitude is now limited by taste, not by
  artifacts.
- Backfill is **lazy on first view**: no sidecar → render static, fire
  `ensure_depth`, swap in when it lands. No batch job, no upfront GPU spike,
  self-healing for every asset already on disk.

## Out of scope

- Video of any kind. WAN I2V, animated WebP, mp4, GIF — all costed and
  rejected in the 2026-08-01 investigation. A separate track if ever wanted.
- HTTP static asset endpoint (only the video route needed it).
- Layered / inpainted depth slabs for large camera moves.
- Pointer- or scroll-driven motion (shader stays capable of it; not wired).
- Avatars, scene cover images.
- Depth maps as first-class library assets.
- DepthFlow as a runtime Python dependency. Phase 0 may invoke its CLI as a
  quality reference bar; shipping it would re-import ffmpeg, a headless GL
  context, and the video delivery problem.

## Acceptance criteria

- AC0: Phase 0 harness renders at least 4 real project assets (≥2 scene
  illustrations, ≥2 character cards) with both shaders, side by side, with
  live amplitude/quality/drift controls. User has recorded a GO/NO-GO and, on
  GO, chosen the amplitude and step-count defaults that phases 1-4 then use.
- AC1: `ensure_depth` on an asset with no sidecar produces
  `<asset_id>.depth.webp`; a second call is a no-op; ten concurrent calls for
  the same id produce exactly one ComfyUI job. Covered by tests.
- AC2: A scene illustration in the message list renders static, then becomes
  animated within a few seconds of first view, with no layout shift and no
  visible pop at the swap.
- AC3: A character card animates inside the same crop it had while static —
  screenshot before/after the swap must show identical framing.
- AC4: Scrolling a scene with 20+ illustrated messages never loses a WebGL
  context and never blanks an image. Verified in both Chrome and Firefox.
- AC5: `prefers-reduced-motion: reduce` and `motion: off` both render exactly
  today's static output. No canvas is created in either case.
- AC6: Deleting an asset removes its depth sidecar. Existing suites green.

## Key files

- `templates/comfyui-workflows/depth.json` (new)
- `src/talemate/agents/visual/agent.py` / `generation.py` (`ensure_depth`)
- `src/talemate/agents/visual/backends/comfyui.py` (reuse `upload_image`,
  `get_images`; note the multi-SaveImage overwrite at 653-663)
- `src/talemate/agents/visual/websocket_handler.py` (`ensure_depth` action)
- `src/talemate/scene_assets.py` (sidecar path helper, delete hook, transfer)
- `talemate_frontend/src/components/ParallaxImage.vue` (new)
- `talemate_frontend/src/components/parallax/` (shader + context pool, new)
- `talemate_frontend/src/components/MessageAssetImage.vue` (integration;
  note the card crop at :289 and the base64 cache at :121-133)
- `scripts/depth_lab/` (phase 0 harness)

## References

- DepthFlow — https://github.com/BrokenSource/DepthFlow (AGPL-3.0, shader at
  `depthflow/resources/depthflow.glsl`)
- Depthy — https://github.com/panrafal/depthy (MIT, the 2014 original)
- Chromium WebGL context limit — https://issues.chromium.org/issues/40939743
- Firefox mobile 2-context limit —
  https://bugzilla.mozilla.org/show_bug.cgi?id=1421481
- virtual-webgl — https://github.com/greggman/virtual-webgl
