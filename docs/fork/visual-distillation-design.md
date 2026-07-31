# Visual prompt distillation

2026-07-31. Status: implemented, default off, awaiting live verification.

## The decision

Stop assembling the image prompt from keyword lists, garment counters and boolean
gates. Hand the structured scene facts to one capable LLM in one call and use its
finished positive and negative prompts. The legacy pipeline stays, untouched, as the
fallback when distillation is off or the call fails.

## Why

The legacy pipeline is a local model writing weak keywords, then Python correcting
them with vocabulary lists. The correction layer (`_subject_is_dressed`,
`_UNDRESS_INTENT_WORDS`, `_CLOTHING_WORDS`, rating gates, wardrobe suppression)
failed in both directions three times in two days:

1. a bare `bare` matched "bare metal walls" → false undress
2. another character's leaked "bare chest" disarmed every nudity negative → false undress
3. garments outnumbering undress phrases put `rating_safe` on a prompt that also
   said "exposed breasts with hardened nipples" → censored an explicit scene

Each fix moved the failure, because the instrument is wrong: keyword counting reasons
over a prompt describing everyone present, when every question is about one person at
one moment. A capable model reading the actual facts does not have this problem.

## Evidence

`scripts/visual_model_bakeoff.py` (results gitignored under
`scripts/bakeoff_results/`). Three fact packs - topless, explicit sex, SFW control -
against every reachable Ollama cloud model, scored for refusal, sanitisation,
inventing explicitness in SFW scenes, and format compliance.

2026-07-31 results: **no model refused or sanitised any tier.** All followed the
two-line contract once the response budget covered their thinking tokens.

- **glm-5.2:cloud — chosen.** Only all-tier PASS at 5–8s/call; tightest output.
- deepseek-v4-pro:cloud — fallback. All-tier PASS at 10–14s; used `explicit`
  instead of `rating_explicit` once.
- qwen3.5:cloud — HTTP 500 on the hardcore tier. Suspected server-side filter. Out.
- gpt-oss:120b-cloud — passed everything, against expectation. Not chosen: its
  alignment posture makes it the most likely to regress on harder content.

Rerun the script when Ollama's model roster changes.

## Architecture

One hook at the top of `GenerationMixin._finalize_prompt` (`generation.py`):

- `request.distilled` → return. The FinalizePrompt preview node and `generate` both
  run finalize over one request; the marker stops a second LLM call and stops the
  legacy surgery from shredding a finished prompt.
- distillation enabled → `_distill_prompt(request)`; success sets the marker and
  returns. Failure of any kind logs and falls through to the legacy pipeline.

`_distill_prompt` gathers the fact pack — subject (via `_choose_subject`), cached
identity anchor, `refresh_wardrobe`, condensed visual rules, sex, scene anchor,
location, other characters' names and sexes, `request.instructions` (the clicked
paragraph), and a `context_history` recap (budget 600, degrades to empty if the
summarizer is unavailable) — and renders `visual/distill-image-prompt.jinja2`, whose
instruction text is the one the bake-off validated. Kind is `visualize_long` (1024
response tokens): plain `visualize` is 150, which a thinking model spends before
writing a single keyword.

The response must be two lines, `PROMPT:` and `NEGATIVE:`
(`parse_distilled_response`). Style template keywords are re-fetched from
`style_template()` and re-applied around the distilled core, because the incoming
prompt string had them flattened in beyond recovery. Dedupe, log, done.

## Configuration

Settings → Agents → Visualizer → **Prompt Distillation** (default off), plus point
the visual agent's text client at a capable model — glm-5.2:cloud via an Ollama
client, per the bake-off. A weak model here reintroduces every problem this exists
to fix. Prose in view of the call (clicked paragraph + recap) leaves the machine
when the client is a cloud model; accepted deliberately (2026-07-31) — the
*director's* prose-free constraint is unchanged.

## What this supersedes (when enabled)

- `visual-subject-fidelity` T16 — the dressed/undressed question disappears; the
  distilling model reads the facts.
- T6 (cull undrawables), T7 (action as one phrase), T8 (budget reduction) — rules 2,
  4 and 6 of the distillation instruction. Verify live before closing them.
- The rating/nudity/sex gate stack and `SCORE_TAGS` experiment become fallback-only
  code. Do not extend them; extend the template.

Known inefficiency, accepted for now: the node graph still pays the local LLM
keyword call whose output distillation discards (it still serves subject-choice
evidence and the DESCRIPTIVE path). Removing it is graph surgery for a later pass.

## Verification

As ever: read the payload actually delivered to ComfyUI from the backend log
(`grep "comfyui.Backend.generate"`, parse `payload=`), not the UI. Look for the
`distill_prompt` debug line carrying the finished prompt and token estimate.
