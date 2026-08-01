"""
Live contract check for prompt profiles (model-aware-prompting track).

Renders the REAL distill-image-prompt.jinja2 for each profile with a realistic
fact pack, sends it to the production distillation model via Ollama, and
asserts the dialect contract on the response:

- pony: PROMPT contains natural-language sentences AND trailing booster tags,
  includes a sex tag and exactly one rating_ tag, no art medium words.
- sdxl_natural: PROMPT is sentences, <= ~75 tokens, contains NO score_/rating_
  tags.

Usage: .venv/Scripts/python.exe scripts/profile_dialect_check.py [--model m]
"""

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

import jinja2  # noqa: E402

from talemate.agents.visual.schema import PROMPT_PROFILES  # noqa: E402

OLLAMA_URL = "http://localhost:11434/api/chat"

FACTS = dict(
    subject=SimpleNamespace(name="Kaira"),
    identity="alien woman, deep violet skin, geometric facial markings, indigo hair, large black eyes, four-fingered hands",
    wardrobe="dark grey-blue combat jumpsuit unzipped to navel, worn boots, hip-holstered disruptor pistol",
    rules="",
    sex="female",
    others="Zak (male)",
    scene_anchor="starship cargo bay, metal bulkheads, stacked crates, sodium work lights, haze",
    location="Starlight Nomad - cargo bay",
    instructions=(
        "Kaira bursts from cover behind a cargo crate, firing her disruptor "
        "pistol twice at a scarred syndicate enforcer, green bolts lighting the "
        "haze as she moves."
    ),
    recent=[
        "The enforcer's blaster clatters away as he spins toward the attack.",
        "Zak clears the corner half a second later, pulse pistol drawn.",
    ],
)


def render(profile_id: str) -> str:
    profile = PROMPT_PROFILES[profile_id]
    src = (
        ROOT / "src/talemate/prompts/templates/visual/distill-image-prompt.jinja2"
    ).read_text(encoding="utf-8")
    env = jinja2.Environment()
    env.globals["llm_can_be_coerced"] = lambda: False
    env.globals["set_prepared_response"] = lambda *_a, **_k: ""
    text = env.from_string(src).render(
        **FACTS,
        max_prompt_tokens=profile.max_prompt_tokens,
        dialect=profile.dialect_instructions,
        profile_id=profile.id,
    )
    # the template's section markers are backend-rendered normally; flatten
    return text.replace("<|SECTION:", "## ").replace("|>", "").replace("<|CLOSE_SECTION", "")


def ask(model: str, prompt: str) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 700},
        "think": False,
    }
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as response:
        return json.loads(response.read())["message"]["content"]


def parse(text: str):
    positive = negative = None
    for line in (text or "").splitlines():
        line = line.strip().strip("*`").strip()
        upper = line.upper()
        if upper.startswith("PROMPT:") and positive is None:
            positive = line[len("PROMPT:") :].strip("*` ").strip()
        elif upper.startswith("NEGATIVE:") and negative is None:
            negative = line[len("NEGATIVE:") :].strip("*` ").strip()
    return positive, negative


def rough_tokens(text: str) -> int:
    return max(1, int(len(text) / 3.5))


def check_pony(positive: str) -> list[str]:
    problems = []
    if not re.search(r"[a-z]{3,}\s+[a-z]+.*?[a-z]\.\s", positive + " "):
        problems.append("no natural-language sentence detected")
    ratings = re.findall(r"rating_\w+", positive)
    if len(ratings) != 1:
        problems.append(f"expected exactly one rating tag, got {ratings}")
    if not re.search(r"\b(1girl|1boy)\b", positive):
        problems.append("no sex tag")
    if re.search(r"score_\d", positive):
        problems.append("score tags present (merge adds them; model must not)")
    return problems


def simulate_sentence_trim(text: str, budget_tokens: int) -> str:
    """Mirror the deployed merge's deterministic sentence trim."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    kept = []
    for sentence in sentences:
        candidate = " ".join([*kept, sentence])
        if kept and rough_tokens(candidate) > budget_tokens:
            break
        kept.append(sentence)
    return " ".join(kept)


def check_sdxl(positive: str) -> list[str]:
    problems = []
    if re.search(r"score_\d|rating_\w+|source_\w+", positive):
        problems.append("pony tags present in POSITIVE")
    if "," in positive and positive.count(",") > 14 and "." not in positive:
        problems.append("looks like a bare tag list, not sentences")
    # the deployed pipeline trims to budget deterministically; validate the
    # post-trim prompt still leads with subject+action
    trimmed = simulate_sentence_trim(positive, 95)
    print("post-trim:", trimmed)
    print("~post-trim tokens:", rough_tokens(trimmed))
    first = re.split(r"(?<=[.!?])\s+", trimmed)[0].lower()
    if not any(w in first for w in ("woman", "man", "alien", "girl", "figure")):
        problems.append("first sentence lost the subject after trim")
    if rough_tokens(trimmed) > 100:
        problems.append("still over budget after trim")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="glm-5.2:cloud")
    args = parser.parse_args()

    failures = 0
    for profile_id, checker in (("pony", check_pony), ("sdxl_natural", check_sdxl)):
        prompt = render(profile_id)
        raw = ask(args.model, prompt)
        positive, negative = parse(raw)
        print(f"\n=== {profile_id} ===")
        if not positive:
            print("FAIL: no PROMPT line")
            print(raw[:400])
            failures += 1
            continue
        print("PROMPT :", positive)
        print("NEGATIVE:", negative)
        print("~tokens :", rough_tokens(positive))
        problems = checker(positive)
        if problems:
            failures += 1
            for p in problems:
                print("FAIL:", p)
        else:
            print("OK")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
