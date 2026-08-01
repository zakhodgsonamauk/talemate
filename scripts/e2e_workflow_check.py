"""
Live workflow verification (comfyui-workflow-quality track).

One real generation through the full stack, then the ComfyUI /history entry
is asserted against: clip skip -2 (pony), rebaked CFG 6.0, background
reference uploaded + sampler rewired onto the style-transfer chain, prompt
delivered verbatim.

Run AFTER the backend restarts with the new code; wins the single frontend
slot by racing the browser's reconnect (same approach as e2e_profile_flow).

Usage: .venv/Scripts/python.exe scripts/e2e_workflow_check.py
"""

import asyncio
import json
from pathlib import Path

import websockets

WS_URL = "ws://localhost:5050/ws"
COMFY = "http://localhost:8188"
ROOT = Path(__file__).parent.parent
SCENE = ROOT / "scenes/infinity-quest-dynamic-story-v2/infinity-quest.json"

PONY_CKPT = "CyberRealisticPony_V9.0_FP16.safetensors"

PROMPT = (
    "score_9, score_8_up, score_7_up, score_6_up, cinematic lighting, "
    "A violet-skinned alien woman stands at a starship console, geometric "
    "facial markings glowing faintly. 1girl, rating_safe, starship bridge"
)


def library_asset_id() -> str:
    lib = json.loads(
        (ROOT / "scenes/infinity-quest-dynamic-story-v2/assets/library.json").read_text(
            encoding="utf-8"
        )
    )
    assets = lib.get("assets", lib)
    for asset_id, asset in assets.items():
        meta = asset.get("meta", asset)
        if meta.get("character_name") == "Kaira":
            return asset_id
    return next(iter(assets))


async def connect_winning_slot():
    for _ in range(120):
        try:
            ws = await websockets.connect(WS_URL, max_size=2**24, open_timeout=5)
        except Exception:
            await asyncio.sleep(0.5)
            continue
        try:
            probe = await asyncio.wait_for(ws.recv(), timeout=2)
            if "already connected" in str(json.loads(probe).get("message", "")):
                await ws.close()
                await asyncio.sleep(0.5)
                continue
        except asyncio.TimeoutError:
            pass
        except Exception:
            await ws.close()
            await asyncio.sleep(0.5)
            continue
        return ws
    raise RuntimeError("could not win the frontend slot")


async def drain_until(ws, predicate, timeout=420):
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if predicate(data):
            return data


def latest_history():
    import urllib.request

    h = json.loads(urllib.request.urlopen(f"{COMFY}/history?max_items=1").read())
    pid, entry = next(iter(h.items()))
    return pid, entry


async def main() -> int:
    failures = []
    bg_asset = library_asset_id()
    print("background reference asset:", bg_asset[:10])

    ws = await connect_winning_slot()
    print("connected (slot won)")
    async with ws:
        await ws.send(json.dumps({"type": "load_scene", "file_path": str(SCENE)}))
        await drain_until(
            ws, lambda d: d.get("type") == "scene_status", timeout=300
        )
        print("scene loaded")
        await asyncio.sleep(3)

        # remember + set checkpoint
        await ws.send(json.dumps({"type": "visual", "action": "checkpoints"}))
        checkpoints = await drain_until(
            ws, lambda d: d.get("type") == "visual" and d.get("action") == "checkpoints"
        )
        original = checkpoints.get("current") or ""
        await ws.send(
            json.dumps(
                {"type": "visual", "action": "set_checkpoint", "checkpoint": PONY_CKPT}
            )
        )
        await asyncio.sleep(1)

        try:
            await ws.send(
                json.dumps(
                    {
                        "type": "visual",
                        "action": "generate",
                        "generation_request": {
                            "prompt": PROMPT,
                            "negative_prompt": "text, watermark, low quality",
                            "vis_type": "SCENE_ILLUSTRATION",
                            "format": "LANDSCAPE",
                            "distilled": True,
                            "prompt_profile": "pony",
                            "background_reference_assets": [bg_asset],
                        },
                    }
                )
            )
            done = await drain_until(
                ws,
                lambda d: d.get("type") == "image_generated"
                or (d.get("type") == "visual" and d.get("action") == "operation_done"),
                timeout=420,
            )
            print("generation finished:", done.get("type"))
        finally:
            await ws.send(
                json.dumps(
                    {"type": "visual", "action": "set_checkpoint", "checkpoint": original}
                )
            )
            await asyncio.sleep(1)
            print("checkpoint restored to:", original or "(workflow default)")

    pid, entry = latest_history()
    graph = entry["prompt"][2]
    status = entry.get("status", {})
    print("history:", pid, "| status:", status.get("status_str"))
    if status.get("status_str") != "success":
        failures.append("generation did not succeed")

    clip_nodes = [
        n for n in graph.values() if n.get("class_type") == "CLIPSetLastLayer"
    ]
    if not clip_nodes:
        failures.append("no CLIPSetLastLayer in submitted graph")
    elif clip_nodes[0]["inputs"]["stop_at_clip_layer"] != -2:
        failures.append(
            f"clip skip expected -2, got {clip_nodes[0]['inputs']['stop_at_clip_layer']}"
        )
    else:
        print("clip skip: -2 OK")

    samplers = [
        (nid, n) for nid, n in graph.items() if "sampler_name" in n.get("inputs", {})
    ]
    for nid, sampler in samplers:
        print(
            "sampler:",
            {k: sampler["inputs"].get(k) for k in ("steps", "cfg", "sampler_name")},
            "| model <-",
            sampler["inputs"].get("model"),
        )
        if sampler["inputs"].get("cfg") not in (6, 6.0):
            failures.append(f"cfg expected 6.0, got {sampler['inputs'].get('cfg')}")
        model_link = sampler["inputs"].get("model")
        apply_bg = next(
            (
                nid2
                for nid2, n2 in graph.items()
                if n2.get("_meta", {}).get("title") == "Apply Background Reference"
            ),
            None,
        )
        if apply_bg and str(model_link[0]) != apply_bg:
            failures.append(
                f"sampler model not rewired to background chain ({model_link} != {apply_bg})"
            )

    bg_load = next(
        (
            n
            for n in graph.values()
            if n.get("_meta", {}).get("title") == "Talemate Background Reference"
        ),
        None,
    )
    if not bg_load:
        failures.append("background reference node missing from submitted graph")
    elif "talemate_bg_" not in str(bg_load["inputs"].get("image")):
        failures.append(
            f"background image not uploaded/bound: {bg_load['inputs'].get('image')}"
        )
    else:
        print("background reference:", bg_load["inputs"]["image"], "OK")

    positive = next(
        (
            n["inputs"].get("text")
            for n in graph.values()
            if n.get("_meta", {}).get("title") == "Talemate Positive Prompt"
        ),
        "",
    )
    if PROMPT.split(",")[4].strip() not in positive:
        failures.append("prompt content not delivered verbatim")
    else:
        print("prompt delivered:", len(positive), "chars")

    print("\nRESULT:", "FAIL" if failures else "PASS")
    for f in failures:
        print(" -", f)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
