"""Minimal ComfyUI HTTP client shared by the depth-lab scripts."""

import json
import mimetypes
import os
import time
import urllib.parse
import urllib.request
import uuid

DEFAULT_API = "http://localhost:8188"


def api(base: str, path: str) -> str:
    return f"{base.rstrip('/')}{path}"


def post_json(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url) as r:
        return json.loads(r.read())


def upload_image(base: str, path_or_bytes, name: str, subfolder: str = "talemate") -> str:
    """POST /upload/image as multipart. Returns 'subfolder/name'."""
    boundary = f"----talemate{uuid.uuid4().hex}"
    if isinstance(path_or_bytes, (bytes, bytearray)):
        data, mime = bytes(path_or_bytes), "image/png"
    else:
        mime = mimetypes.guess_type(path_or_bytes)[0] or "image/png"
        with open(path_or_bytes, "rb") as f:
            data = f.read()

    parts = [
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="image"; '
            f'filename="{name}"\r\nContent-Type: {mime}\r\n\r\n'
        ).encode("utf-8"),
        data,
        b"\r\n",
    ]
    for key, value in (("type", "input"), ("subfolder", subfolder), ("overwrite", "true")):
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n'
            f"{value}\r\n".encode("utf-8")
        )
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


def fetch_output(base: str, item: dict) -> bytes:
    q = urllib.parse.urlencode(
        {
            "filename": item["filename"],
            "subfolder": item.get("subfolder", ""),
            "type": item.get("type", "output"),
        }
    )
    with urllib.request.urlopen(api(base, f"/view?{q}")) as r:
        return r.read()


def submit(base: str, workflow: dict) -> str:
    return post_json(api(base, "/prompt"), {"prompt": workflow})["prompt_id"]


def wait(base: str, prompt_id: str, timeout: float = 900.0) -> dict:
    start = time.time()
    while True:
        history = get_json(api(base, f"/history/{prompt_id}"))
        if history:
            return history[prompt_id]["outputs"]
        if time.time() - start > timeout:
            raise TimeoutError(f"job {prompt_id} did not finish in {timeout}s")
        time.sleep(0.5)


def collect(base: str, outputs: dict, keys=("images", "gifs", "videos")) -> list[tuple[str, bytes]]:
    """
    Every output file across every node, as (filename, bytes).

    Video nodes report under "gifs" even when the file is an mp4 - the reason
    the product code's get_images misses them today.
    """
    found = []
    for node_output in outputs.values():
        for key in keys:
            for item in node_output.get(key, []) or []:
                found.append((item["filename"], fetch_output(base, item)))
    return found


def run(base: str, workflow: dict, timeout: float = 900.0) -> list[tuple[str, bytes]]:
    return collect(base, wait(base, submit(base, workflow), timeout))


def load_workflow(name: str) -> dict:
    path = os.path.join(os.path.dirname(__file__), "workflows", name)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def set_by_title(workflow: dict, title: str, **inputs) -> None:
    for node in workflow.values():
        if node.get("_meta", {}).get("title") == title:
            node["inputs"].update(inputs)
            return
    raise KeyError(f"no node titled {title!r}")
