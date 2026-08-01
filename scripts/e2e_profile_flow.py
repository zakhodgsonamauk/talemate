"""
End-to-end profile flow check against a RUNNING backend.

Own websocket session, own throwaway copy of the lab test scene - does not
touch any scene a browser session has open. Exercises: profile resolution ->
dialect distillation -> profile-aware merge -> FinalizePrompt -> prompt_preview
payload (including the prompt_profile key the modal recompose relies on).

Restores the agent's configured checkpoint afterwards.

Usage: .venv/Scripts/python.exe scripts/e2e_profile_flow.py
"""

import asyncio
import json
import re
import sys
from pathlib import Path

import websockets

WS_URL = "ws://localhost:5050/ws"
ROOT = Path(__file__).parent.parent
SCENE = ROOT / "scenes/infinity-quest-dynamic-story-v2/infinity-quest.json"

PONY_CKPT = "CyberRealisticPony_V9.0_FP16.safetensors"
SDXL_CKPT = "Juggernaut-XI-byRunDiffusion.safetensors"

INSTRUCTIONS = (
    "A scientist leans over the lab bench, examining a glowing device, "
    "sparks reflecting in their goggles."
)


async def drain_until(ws, predicate, timeout=180, label=""):
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if predicate(data):
            return data


async def compose(ws, label):
    await ws.send(
        json.dumps(
            {
                "type": "visual",
                "action": "visualize",
                "vis_type": "SCENE_ILLUSTRATION",
                "prompt_only": True,
                "return_prompt": True,
                "instructions": INSTRUCTIONS,
                "message_ids": [],
            }
        )
    )
    preview = await drain_until(
        ws,
        lambda d: d.get("type") == "visual" and d.get("action") == "prompt_preview",
        timeout=240,
        label=label,
    )
    return preview


def rough_tokens(text: str) -> int:
    return max(1, int(len(text) / 3.5))


async def connect_winning_slot():
    """
    The server accepts ONE frontend; a browser tab may be auto-reconnecting.
    Retry until we take the slot (a rejected connection gets a system error
    message then close).
    """
    for attempt in range(120):
        try:
            ws = await websockets.connect(WS_URL, max_size=2**24, open_timeout=5)
        except Exception:
            await asyncio.sleep(0.5)
            continue
        try:
            probe = await asyncio.wait_for(ws.recv(), timeout=2)
            data = json.loads(probe)
            if "already connected" in str(data.get("message", "")):
                await ws.close()
                await asyncio.sleep(0.5)
                continue
        except asyncio.TimeoutError:
            pass  # no rejection = slot is ours
        except Exception:
            await ws.close()
            await asyncio.sleep(0.5)
            continue
        return ws
    raise RuntimeError("could not win the frontend slot")


async def main() -> int:
    failures = []
    ws = await connect_winning_slot()
    print("connected (slot won)")
    async with ws:
        # load an isolated scene
        await ws.send(
            json.dumps({"type": "load_scene", "file_path": str(SCENE)})
        )
        await drain_until(
            ws,
            lambda d: d.get("type") == "system"
            and d.get("id") == "scene.loaded"
            or d.get("type") == "scene_status",
            timeout=300,
            label="scene load",
        )
        print("scene loaded")
        await asyncio.sleep(3)

        # remember current checkpoint to restore
        await ws.send(json.dumps({"type": "visual", "action": "checkpoints"}))
        checkpoints = await drain_until(
            ws,
            lambda d: d.get("type") == "visual" and d.get("action") == "checkpoints",
            timeout=60,
        )
        original = checkpoints.get("current") or ""
        profiles = checkpoints.get("profiles") or {}
        print("current checkpoint:", original or "(workflow default)")
        print("profile map entries:", len(profiles))
        if PONY_CKPT in profiles:
            assert profiles[PONY_CKPT] == "pony", profiles[PONY_CKPT]
        if SDXL_CKPT in profiles:
            assert profiles[SDXL_CKPT] == "sdxl_natural", profiles[SDXL_CKPT]

        try:
            for ckpt, expected_profile in (
                (PONY_CKPT, "pony"),
                (SDXL_CKPT, "sdxl_natural"),
            ):
                await ws.send(
                    json.dumps(
                        {"type": "visual", "action": "set_checkpoint", "checkpoint": ckpt}
                    )
                )
                await asyncio.sleep(1)
                preview = await compose(ws, expected_profile)
                prompt = preview.get("prompt") or ""
                profile = preview.get("prompt_profile") or ""
                print(f"\n=== {expected_profile} ({ckpt}) ===")
                print("profile:", profile)
                print("prompt :", prompt[:400])
                print("~tokens:", rough_tokens(prompt))

                if profile != expected_profile:
                    failures.append(
                        f"{ckpt}: expected profile {expected_profile}, got {profile}"
                    )
                if not prompt.strip():
                    failures.append(f"{ckpt}: empty prompt")
                    continue
                if expected_profile == "pony":
                    for tag in ("score_9", "score_8_up", "score_7_up", "score_6_up"):
                        if tag not in prompt:
                            failures.append(f"pony: missing {tag}")
                    if not re.search(r"rating_\w+", prompt):
                        failures.append("pony: no rating tag")
                else:
                    if re.search(r"score_\d|rating_\w+", prompt):
                        failures.append("sdxl: pony tags leaked into prompt")
                    if rough_tokens(prompt) > 120:
                        failures.append(
                            f"sdxl: over budget ~{rough_tokens(prompt)} rough tokens"
                        )
        finally:
            await ws.send(
                json.dumps(
                    {
                        "type": "visual",
                        "action": "set_checkpoint",
                        "checkpoint": original,
                    }
                )
            )
            await asyncio.sleep(1)
            print("\ncheckpoint restored to:", original or "(workflow default)")

    print("\nRESULT:", "FAIL" if failures else "PASS")
    for failure in failures:
        print(" -", failure)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
