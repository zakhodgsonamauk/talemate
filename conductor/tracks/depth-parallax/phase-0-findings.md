# Phase 0 findings — depth parallax lab

Run 2026-08-01 on the project box (RTX 4080 16GB, ComfyUI 0.7.0 at :8188,
Chromium 150 via Playwright).

Artifacts live in the scratchpad, not the repo:
`…/scratchpad/depth_lab/` — 5 image+depth pairs, `lab.html`, `shots/`.
Serve with `python -m http.server 8777 --directory <that dir>` and open
`http://localhost:8777/lab.html`.

## T0.1 — depth generation

`templates/comfyui-workflows/depth.json` (LoadImage → DepthAnythingV2 →
ImageScale → SaveImage) run over 5 real assets from
`infinity-quest-dynamic-story-v2`: 2 scene illustrations, 3 character cards.

| finding | value |
| --- | --- |
| depth polarity | **near = white** |
| output dimensions | exactly source WxH (the ImageScale node forces it) |
| output mode | RGB PNG, 157-255 KB |
| first run | **184.8 s** (`depth_anything_v2_vitl.pth` load) |
| steady state | **10.6-10.7 s per asset**, very consistent |

**Correction to the spec.** I estimated 1-2s. It is ~10.7s, because
controlnet_aux loads and frees the model per execution rather than keeping it
resident. Still fine for lazy backfill — nothing blocks on it — but a 200-asset
library is ~35 min of background GPU, not 5. Worth saying out loud before
anyone offers a "backfill everything" button.

Node titles were chosen so phase 1 gets plumbing for free: the LoadImage node is
`Talemate Reference 1` (bound by the existing `set_reference_images`) and the
ImageScale node is `Talemate Resolution` (bound by the existing
`set_resolution`). Caveat for phase 1: `set_reference_images([])` disconnects
unpopulated reference nodes, so the depth path must always pass exactly one
path.

Depth quality on our art is very good — crisp subject/background separation,
smooth interior gradients. Note that our illustrations often have a bokeh'd
background, so depth is mostly "subject vs void"; the effect reads as the
subject sliding against the backdrop rather than as a rich multi-plane scene.

## T0.2 — naive vs ray-marched

Identical asset, identical depth, identical fixed camera offset.

- **2% amplitude** — indistinguishable. At this strength the cheap shader is
  genuinely fine.
- **8% amplitude** — clear separation. Naive visibly widens the subject: arms
  elongate, the figure gets fatter, the left gun smears into the background.
  Ray-marching holds proportions correctly; the figure stays the right shape.
- **~10%+** — ray-marching holds geometry but grows trailing silhouette
  streaks (see below).

**Verdict: ray-marching wins, and it is not a close call above ~4%.**

## Trailing-edge streaking, and the fix

Raised during the run. The streaks are silhouette starvation: where the ray
crosses a depth cliff there is simply no image data behind the edge, so the
march lands on the last texel and repeats it along the direction of travel.

Two mitigations, both tested at **8% amplitude** (well past shipping strength,
chosen so the effect is unmistakable):

1. **Blur the depth map (5px).** Softens the discontinuity so a hard streak
   becomes a gentle stretch. Removed nearly all visible streaking on its own.
   Costs **nothing at runtime** — it belongs in the sidecar writer, applied
   once at generation time, and every client benefits.
2. **Edge-aware soften in the shader.** Measure how violently depth changed
   over the hit step (`|h - hPrev| / dz`); where that exceeds a smooth slope,
   sample a coarser mip. The streak becomes a soft smudge that reads as
   depth-of-field. Benchmarked at **no measurable cost** (0.255 ms vs 0.295 ms
   at 1.12 MP / 64 steps — the mip fetches are cache-friendlier than the base
   level, so it came out marginally *faster*, i.e. it is free).

Together at 8%: streaks gone, residual is a soft halo at the silhouette that
reads as bloom. At the proposed shipping 3% the output is clean enough to be
indistinguishable from the original still.

Screenshots: `shots/fix-0-baseline.png`, `shots/fix-1-depthblur.png`,
`shots/fix-2-both.png`, `shots/ship-3pct.png`.

**This changes the amplitude ceiling again.** The spec said naive breaks at ~6%
and ray-marching lifts it. With both mitigations, 8% is usable and 3% is
flawless — so the constraint on amplitude is now taste, not artifacts.

## T0.3 — performance

vsync pins everything at 120 fps on this GPU, so cost was measured directly:
N draws followed by a `readPixels` to drain the pipeline. Canvas 1280x875
(1.12 MP), which is far larger than any real display size.

| march steps | ray-marched ms/frame | ms per megapixel |
| --- | --- | --- |
| 16 | 0.090 | 0.080 |
| 32 | 0.138 | 0.123 |
| 64 | 0.253 | 0.225 |
| 128 | 0.537 | 0.480 |
| 256 | 0.997 | 0.891 |

Naive is 0.017-0.035 ms regardless of steps, as expected.

Linear in step count, as expected. At a realistic illustration size
(700x400 = 0.28 MP) 64 steps costs **0.063 ms** on this GPU. A client GPU 20x
slower still lands at ~1.3 ms — comfortably inside a 16 ms budget with two
panes live. **Step count is not a constraint; 64 is a safe default and 128 is
affordable.**

Caveat stated plainly: this is a 4080. The lab cannot tell us what an Intel
iGPU laptop does. The ms/megapixel column is there so that number can be
scaled when someone tests on weaker hardware.

## T0.3b — WebGL2 context ceiling (Chromium 150)

| requested | acquired | refused | contextlost events |
| --- | --- | --- | --- |
| 8 | 8 | 0 | 0 |
| 16 | 16 | 0 | 0 |
| 24 | 24 | 0 | **8** |
| 40 | 40 | 0 | **24** |

Confirms the documented behaviour and the reason pooling is mandatory: Chromium
never *refuses* a context, it silently kills the oldest ones. Past 16 live
contexts, canvases start going blank with no error — exactly the failure mode
that would make a scrolled scene lose its images. The pool of 1-2 in the plan
is correct.

**Not tested: Firefox** (Playwright here runs Chromium only). Documented limits
are 8 per principal desktop and **2 on mobile**, which is stricter than what we
measured. The plan's AC4 keeps the Firefox check as a manual step; that stands.

## Card crop parity

The 3:4 top-anchored cover crop that `MessageAssetImage.vue:289` applies to
character cards was reproduced in the shader's UV setup and verified against a
768x1344 card at 250px: `uvScale [1, 0.761]`, `uvOffset [0, 0]`. Framing matches
and parallax works inside the crop. The trap flagged in the spec is solved —
the maths is in `coverCrop()` in `lab.html` and transfers directly to phase 2.

## T0.4 — DepthFlow CLI reference

Skipped. The two mitigations closed the quality gap that this comparison
existed to measure, and the timebox was better spent proving them. The shader
port itself is already derived from DepthFlow's approach and carries its AGPL
attribution.

## Recommended defaults for phases 1-4

| parameter | value | why |
| --- | --- | --- |
| shader | ray-marched | clear win above ~4% |
| march steps | 64 | 0.063 ms at real size; 128 affordable if wanted |
| depth blur | 5 px, baked into the sidecar | kills streaks, free at runtime |
| edge soften | 0.7 | free, turns residual streaks into bloom |
| overscan | 4% | keeps displaced samples inside the frame |
| amplitude — illustrations | 3% | flawless; 8% is available if you want more |
| amplitude — cards | 2% | tighter crop, less depth range |
| drift | Lissajous sin(0.31t), sin(0.23t) | no perceptible loop point |
| devicePixelRatio | 1 | cost control; no visible penalty at these sizes |

## Spec amendments this run produced

1. Depth generation is ~10.7 s per asset, not 1-2 s (R1).
2. The sidecar writer must blur the depth map by ~5 px before saving (R2) —
   this is a new requirement, and it is the single highest-value line of code
   in the whole track.
3. The shader needs a `uSoften` uniform and a mip chain on the image texture
   (R4).
4. Amplitude is no longer artifact-limited at the values we care about.

## Gate

Everything phase 0 set out to answer is answered, and the answers are
favourable. **Awaiting GO/NO-GO.**
