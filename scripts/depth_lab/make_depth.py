"""
Phase-0 lab tool: generate depth maps for real scene assets via ComfyUI.

Standalone on purpose. It talks to ComfyUI directly rather than going through
the visual agent, so the lab can run without a backend restart and without any
product code existing yet. Phase 1 replaces it with VisualAgent.ensure_depth.

Usage (from the repo root):

    python scripts/depth_lab/make_depth.py --scene infinity-quest-dynamic-story-v2
    python scripts/depth_lab/make_depth.py --scene <name> --asset <id> --asset <id>
    python scripts/depth_lab/make_depth.py --scene <name> --out C:/somewhere

Writes <out>/<asset_id>.<ext> and <out>/<asset_id>.depth.png plus a
manifest.json the lab page reads.
"""

import argparse
import json
import mimetypes
import os
import shutil
import sys
import time
import urllib.parse
import urllib.request
import uuid

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WORKFLOW_PATH = os.path.join(
    REPO_ROOT, "templates", "comfyui-workflows", "depth.json"
)
DEFAULT_API = "http://localhost:8188"

# Enough of a spread to judge the effect: wide landscape illustrations and
# tall 3:4-ish character cards, which crop differently in the message list.
WANTED_VIS_TYPES = ("SCENE_ILLUSTRATION", "CHARACTER_CARD")


def api(base: str, path: str) -> str:
    return f"{base.rstrip('/')}{path}"


def post_json(url: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url) as r:
        return json.loads(r.read())


def upload_image(base: str, path: str, name: str) -> str:
    """POST /upload/image as multipart. Returns the 'subfolder/name' path."""
    boundary = f"----talemate{uuid.uuid4().hex}"
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as f:
        data = f.read()

    parts = []

    def field(key: str, value: str):
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n'
            f"{value}\r\n".encode("utf-8")
        )

    parts.append(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="image"; '
            f'filename="{name}"\r\nContent-Type: {mime}\r\n\r\n'
        ).encode("utf-8")
    )
    parts.append(data)
    parts.append(b"\r\n")
    field("type", "input")
    field("subfolder", "talemate")
    field("overwrite", "true")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))

    req = urllib.request.Request(
        api(base, "/upload/image"),
        data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req) as r:
        out = json.loads(r.read())
    sub = out.get("subfolder") or ""
    return f"{sub}/{out['name']}" if sub else out["name"]


def fetch_output_image(base: str, image: dict) -> bytes:
    q = urllib.parse.urlencode(
        {
            "filename": image["filename"],
            "subfolder": image.get("subfolder", ""),
            "type": image.get("type", "output"),
        }
    )
    with urllib.request.urlopen(api(base, f"/view?{q}")) as r:
        return r.read()


def run_depth(base: str, uploaded_path: str, width: int, height: int) -> bytes:
    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        wf = json.load(f)

    # Mirrors what Workflow.set_reference_images / set_resolution will do in
    # phase 1 - the node titles are chosen so that reuse is free.
    for node in wf.values():
        title = node.get("_meta", {}).get("title", "")
        if title == "Talemate Reference 1":
            node["inputs"]["image"] = uploaded_path
        elif title == "Talemate Resolution":
            node["inputs"]["width"] = width
            node["inputs"]["height"] = height

    prompt_id = post_json(api(base, "/prompt"), {"prompt": wf})["prompt_id"]

    start = time.time()
    while True:
        history = get_json(api(base, f"/history/{prompt_id}"))
        if history:
            break
        if time.time() - start > 180:
            raise TimeoutError(f"depth job {prompt_id} did not finish in 180s")
        time.sleep(0.5)

    outputs = history[prompt_id]["outputs"]
    for node_output in outputs.values():
        for image in node_output.get("images", []):
            return fetch_output_image(base, image)
    raise RuntimeError(f"no image in outputs for {prompt_id}: {outputs}")


def pick_assets(library: dict, explicit: list[str], limit: int) -> list[tuple]:
    assets = library["assets"]
    if explicit:
        return [(aid, assets[aid]) for aid in explicit if aid in assets]

    chosen = []
    for vis_type in WANTED_VIS_TYPES:
        matching = [
            (aid, a)
            for aid, a in assets.items()
            if a.get("meta", {}).get("vis_type") == vis_type
        ]
        chosen.extend(matching[:limit])
    return chosen


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--scene", required=True, help="scene directory name under scenes/")
    p.add_argument("--asset", action="append", default=[], help="explicit asset id")
    p.add_argument("--limit", type=int, default=3, help="per vis_type when auto-picking")
    p.add_argument("--api", default=DEFAULT_API)
    p.add_argument("--out", default=None, help="output dir (default: ./depth_lab_out)")
    args = p.parse_args()

    asset_dir = os.path.join(REPO_ROOT, "scenes", args.scene, "assets")
    library_path = os.path.join(asset_dir, "library.json")
    if not os.path.exists(library_path):
        print(f"no library.json at {library_path}", file=sys.stderr)
        return 1

    out_dir = args.out or os.path.join(os.getcwd(), "depth_lab_out")
    os.makedirs(out_dir, exist_ok=True)

    with open(library_path, "r", encoding="utf-8") as f:
        library = json.load(f)

    targets = pick_assets(library, args.asset, args.limit)
    if not targets:
        print("no matching assets", file=sys.stderr)
        return 1

    manifest = []
    for aid, asset in targets:
        ext = asset["file_type"]
        src = os.path.join(asset_dir, f"{aid}.{ext}")
        if not os.path.exists(src):
            print(f"  skip {aid[:10]} - file missing")
            continue

        res = asset.get("meta", {}).get("resolution") or {}
        width, height = res.get("width"), res.get("height")
        if not width or not height:
            from PIL import Image

            with Image.open(src) as im:
                width, height = im.size

        vis_type = asset.get("meta", {}).get("vis_type")
        print(f"  {aid[:10]} {vis_type} {width}x{height} ... ", end="", flush=True)

        t0 = time.time()
        uploaded = upload_image(args.api, src, f"depthlab_{aid[:10]}.{ext}")
        depth_bytes = run_depth(args.api, uploaded, width, height)
        elapsed = time.time() - t0

        with open(src, "rb") as fsrc:
            image_bytes = fsrc.read()
        with open(os.path.join(out_dir, f"{aid}.{ext}"), "wb") as f:
            f.write(image_bytes)
        with open(os.path.join(out_dir, f"{aid}.depth.png"), "wb") as f:
            f.write(depth_bytes)

        print(f"{elapsed:.1f}s")
        manifest.append(
            {
                "id": aid,
                "image": f"{aid}.{ext}",
                "depth": f"{aid}.depth.png",
                "vis_type": vis_type,
                "width": width,
                "height": height,
                "seconds": round(elapsed, 2),
            }
        )

    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    # The page has to be served from the same directory as the images: WebGL
    # refuses to upload a file:// image as a texture.
    shutil.copy(os.path.join(os.path.dirname(__file__), "lab.html"), out_dir)

    print(f"\n{len(manifest)} pairs -> {out_dir}")
    print("serve it with:")
    print(f'  python -m http.server 8777 --directory "{out_dir}"')
    print("  then open http://localhost:8777/lab.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
