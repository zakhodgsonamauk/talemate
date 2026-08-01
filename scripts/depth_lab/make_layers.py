"""
Phase-0 lab tool (rungs 3 and 4): inpainted background plates for layered
parallax.

Single-layer parallax cannot slide far because there is nothing painted behind
the subject - the shader can only hide that hole, not fill it. This produces
the fill: threshold the depth map into slabs, and have LaMa remove each near
slab so the layer behind it has real pixels.

LaMa (AILab_LamaRemover) is used rather than a diffusion inpaint because it
needs no prompt, no checkpoint swap and no sampler - it is seconds, not
minutes, which keeps this a post-process rather than a second generation.

    python scripts/depth_lab/make_layers.py --dir <lab_out> --layers 3

Writes <id>.plate1.png (behind the near slab) and, with --layers 3,
<id>.plate2.png (behind near+mid), and records thresholds in manifest.json.
"""

import argparse
import io
import json
import os
import sys
import time

import numpy as np
from PIL import Image, ImageFilter

sys.path.insert(0, os.path.dirname(__file__))
import comfy_client as cc  # noqa: E402


def otsu(gray: np.ndarray) -> float:
    """Threshold in 0..1 that best splits the depth histogram in two."""
    hist, _ = np.histogram(gray, bins=256, range=(0.0, 1.0))
    total = hist.sum()
    if total == 0:
        return 0.5
    bins = (np.arange(256) + 0.5) / 256.0
    w0 = np.cumsum(hist)
    w1 = total - w0
    valid = (w0 > 0) & (w1 > 0)
    sum_all = float((hist * bins).sum())
    sum0 = np.cumsum(hist * bins)
    mean0 = np.divide(sum0, w0, out=np.zeros_like(sum0), where=w0 > 0)
    mean1 = np.divide(sum_all - sum0, w1, out=np.zeros_like(sum0), where=w1 > 0)
    between = w0 * w1 * (mean0 - mean1) ** 2
    between[~valid] = -1.0
    return float(bins[int(np.argmax(between))])


def slab_mask(depth: np.ndarray, threshold: float, dilate_px: int, feather_px: int) -> Image.Image:
    """White where depth >= threshold, grown and feathered, as an L-mode image."""
    mask = ((depth >= threshold) * 255).astype(np.uint8)
    img = Image.fromarray(mask, mode="L")
    if dilate_px > 0:
        # MaxFilter needs an odd kernel; grow so the plate covers the whole
        # silhouette including its soft edge, or LaMa leaves a halo of the
        # subject baked into the background.
        k = dilate_px * 2 + 1
        img = img.filter(ImageFilter.MaxFilter(min(k, 99)))
    if feather_px > 0:
        img = img.filter(ImageFilter.GaussianBlur(feather_px))
    return img


def lama_plate(base: str, source_path: str, mask_img: Image.Image, tag: str,
               strength: int, smoothness: int) -> bytes:
    buf = io.BytesIO()
    mask_img.convert("RGB").save(buf, format="PNG")

    src = cc.upload_image(base, source_path, f"lab_src_{tag}.png")
    msk = cc.upload_image(base, buf.getvalue(), f"lab_mask_{tag}.png")

    wf = cc.load_workflow("bg_lama.json")
    cc.set_by_title(wf, "Lab Source Image", image=src)
    cc.set_by_title(wf, "Lab Slab Mask", image=msk)
    cc.set_by_title(
        wf, "Lab LaMa Background Plate",
        removal_strength=strength, edge_smoothness=smoothness,
    )
    out = cc.run(base, wf, timeout=300)
    if not out:
        raise RuntimeError(f"LaMa produced no output for {tag}")
    return out[0][1]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True, help="lab output dir holding manifest.json")
    p.add_argument("--layers", type=int, default=3, choices=(2, 3))
    p.add_argument("--api", default=cc.DEFAULT_API)
    p.add_argument("--dilate", type=int, default=None, help="px, default 1.5%% of width")
    p.add_argument("--feather", type=int, default=4)
    p.add_argument("--strength", type=int, default=230)
    p.add_argument("--smoothness", type=int, default=8)
    p.add_argument("--only", action="append", default=[], help="asset id prefix filter")
    args = p.parse_args()

    manifest_path = os.path.join(args.dir, "manifest.json")
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    for entry in manifest:
        if args.only and not any(entry["id"].startswith(o) for o in args.only):
            continue

        src = os.path.join(args.dir, entry["image"])
        depth_img = Image.open(os.path.join(args.dir, entry["depth"])).convert("L")
        depth = np.asarray(depth_img, dtype=np.float32) / 255.0

        # Near slab: Otsu splits subject from background on our art, where the
        # histogram really is bimodal (bokeh'd backdrop, solid subject).
        t_near = otsu(depth)
        # Mid boundary: halfway down the remaining background range, which is
        # where a floor or a mid-ground prop tends to sit.
        t_mid = float(np.percentile(depth[depth < t_near], 70)) if (depth < t_near).any() else t_near * 0.5

        dilate = args.dilate if args.dilate is not None else max(4, int(entry["width"] * 0.015))
        tag = entry["id"][:10]
        print(f"  {tag} t_near={t_near:.3f} t_mid={t_mid:.3f} dilate={dilate}px ... ", end="", flush=True)

        t0 = time.time()
        plates = {}

        m1 = slab_mask(depth, t_near, dilate, args.feather)
        plate1 = lama_plate(args.api, src, m1, f"{tag}_p1", args.strength, args.smoothness)
        with open(os.path.join(args.dir, f"{entry['id']}.plate1.png"), "wb") as f:
            f.write(plate1)
        plates["plate1"] = f"{entry['id']}.plate1.png"
        plates["plate1_bytes"] = len(plate1)

        if args.layers == 3:
            m2 = slab_mask(depth, t_mid, dilate, args.feather)
            plate2 = lama_plate(args.api, src, m2, f"{tag}_p2", args.strength, args.smoothness)
            with open(os.path.join(args.dir, f"{entry['id']}.plate2.png"), "wb") as f:
                f.write(plate2)
            plates["plate2"] = f"{entry['id']}.plate2.png"
            plates["plate2_bytes"] = len(plate2)

        elapsed = time.time() - t0
        entry.update(plates)
        entry["t_near"] = round(t_near, 4)
        entry["t_mid"] = round(t_mid, 4)
        entry["plate_seconds"] = round(elapsed, 2)
        print(f"{elapsed:.1f}s")

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"\nplates -> {args.dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
