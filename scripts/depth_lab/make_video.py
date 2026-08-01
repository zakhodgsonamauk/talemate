"""
Phase-0 lab tool (rung 5): WAN 2.2 image-to-video clips from the same still.

The top of the ladder. Everything below it is geometry over one painted frame;
this is a diffusion model inventing new frames, which is why it costs minutes
instead of milliseconds and why it can move a subject rather than only a camera.

    python scripts/depth_lab/make_video.py --dir <lab_out> --only 40040ea8
    python scripts/depth_lab/make_video.py --dir <lab_out> --kind camera

Writes <id>.wan_i2v.mp4 (or .wan_camera.mp4) and records the wall clock in
manifest.json so the comparison page can show what each rung actually cost.
"""

import argparse
import json
import os
import sys
import time

from PIL import Image

sys.path.insert(0, os.path.dirname(__file__))
import comfy_client as cc  # noqa: E402

WORKFLOWS = {
    "i2v": ("wan_i2v_rapid.json", "wan_i2v"),
    "camera": ("wan_camera.json", "wan_camera"),
}

# WAN wants dimensions on a 16px grid, and 480p-class resolutions keep the
# clip inside the VRAM this box has spare with ComfyUI reserving 5GB.
MAX_EDGE = 832
MIN_EDGE = 480


def target_size(width: int, height: int, max_edge: int = MAX_EDGE, min_edge: int = MIN_EDGE) -> tuple[int, int]:
    scale = min(max_edge / max(width, height), min_edge / min(width, height))
    w = max(16, int(round(width * scale / 16)) * 16)
    h = max(16, int(round(height * scale / 16)) * 16)
    return w, h


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True)
    p.add_argument("--kind", default="i2v", choices=tuple(WORKFLOWS))
    p.add_argument("--api", default=cc.DEFAULT_API)
    p.add_argument("--only", action="append", default=[])
    p.add_argument("--length", type=int, default=49, help="frames; 49 @16fps = ~3s")
    p.add_argument("--steps", type=int, default=4)
    p.add_argument("--prompt", default=None)
    p.add_argument("--timeout", type=float, default=2400.0)
    p.add_argument("--max-edge", type=int, default=MAX_EDGE,
                   help="shrink for fast iteration; a failed 480x320 attempt costs "
                        "minutes where a failed 832x480 one costs twenty")
    p.add_argument("--min-edge", type=int, default=MIN_EDGE)
    p.add_argument("--suffix", default="", help="tag added to the manifest key, e.g. 'small'")
    args = p.parse_args()

    workflow_name, manifest_key = WORKFLOWS[args.kind]
    manifest_path = os.path.join(args.dir, "manifest.json")
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    for entry in manifest:
        if args.only and not any(entry["id"].startswith(o) for o in args.only):
            continue

        src = os.path.join(args.dir, entry["image"])
        with Image.open(src) as im:
            w, h = target_size(*im.size, args.max_edge, args.min_edge)

        tag = entry["id"][:10]
        print(f"  {tag} {args.kind} {w}x{h} x{args.length}f ... ", end="", flush=True)

        key = manifest_key + (f"_{args.suffix}" if args.suffix else "")
        uploaded = cc.upload_image(args.api, src, f"lab_wan_{tag}.png")
        wf = cc.load_workflow(workflow_name)
        cc.set_by_title(wf, "Lab Source Image", image=uploaded)
        cc.set_by_title(wf, "Lab WAN I2V", width=w, height=h, length=args.length)
        cc.set_by_title(wf, "Lab WAN Sampler", steps=args.steps)
        if args.prompt:
            cc.set_by_title(wf, "Lab WAN Positive", text=args.prompt)

        t0 = time.time()
        try:
            outputs = cc.run(args.api, wf, timeout=args.timeout)
        except Exception as e:
            print(f"FAILED: {e}")
            continue
        elapsed = time.time() - t0

        video = next(((n, b) for n, b in outputs if n.lower().endswith((".mp4", ".webm"))), None)
        if not video:
            print(f"no video in outputs ({[n for n, _ in outputs]})")
            continue

        ext = os.path.splitext(video[0])[1]
        out_name = f"{entry['id']}.{key}{ext}"
        with open(os.path.join(args.dir, out_name), "wb") as f:
            f.write(video[1])

        entry[key] = out_name
        entry[f"{key}_seconds"] = round(elapsed, 1)
        entry[f"{key}_bytes"] = len(video[1])
        print(f"{elapsed:.0f}s  {len(video[1])/1e6:.2f} MB -> {out_name}")

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
