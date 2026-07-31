# Model-aware prompt generation — specification

## Goal

Image prompts are composed before the checkpoint is known, in a single dialect.
Observed live (flow visual:revisualize:fcbc1f, 2026-08-01): a Pony-style
keyword prompt (`score_9, score_8_up, ...`) was sent to
`Juggernaut-XI-byRunDiffusion.safetensors` — a model that wants component-
ordered natural language under 75 tokens and treats score tags as noise.

Invert the order: resolve backend + checkpoint first, attach a **prompt
profile** to the request, and compose the prompt in that profile's dialect.

## Dialect ground truth (user-supplied guides, 2026-08-01)

### Pony (family: Pony Diffusion XL, CyberRealistic Pony, ...)

Four-section structure, in order:
1. **Special tags** — ALL FOUR quality tags stacked: `score_9, score_8_up,
   score_7_up, score_6_up` (score_9 alone is not enough — the current
   pipeline emits only three; add score_6_up), plus exactly one rating:
   `rating_safe` / `rating_questionable` / `rating_explicit`. Optional
   `source_*` tag only when targeting an aesthetic source.
2. **Factual description** — 1-2 NATURAL-LANGUAGE paragraphs (Five W's: who,
   what, where & when, why). Primary subject detailed, secondary elements
   concise. Pony handles long descriptions well — this is not a tag-only
   model, and the current pure-keyword output is suboptimal even for Pony.
3. **Stylistic description** — lighting, art style/medium, composition,
   palette, atmosphere. **An explicit art style/medium is REQUIRED** (Pony
   has no default style; e.g. digital illustration, photograph, oil painting).
4. **Booster tags** — comma-separated short tags reinforcing key concepts.

Emphasis: position > booster tags > weights. Weights sparingly, max ~1.3.

### sdxl_natural (family: Juggernaut XI/XII, RealVis, ...)

- Natural-language sentences (tags tolerated), component order: subject →
  action → environment → objects → color → style → mood → lighting →
  perspective → texture → clothing. **First sentence carries the most
  weight.**
- **≤ 75 tokens — exceeding reduces adherence** (current Pony-length prompts
  are 2x+ over).
- Trigger words: "High Resolution", "Cinematic", concrete textures.
- Weights sparingly, max 1.4.
- Negative base: artifact-oriented ("deformed eyes, bad eyes, cgi, 3D,
  airbrushed"; "bad hands" for XII) — not the Pony convention.
- Sampler guidance (informational; workflow profiles already exist):
  DPM++ 2M Karras, steps 25-40, CFG 4-6.

### descriptive

The existing prose path for image-edit/DESCRIPTIVE backends. Unchanged.

### Supplementary reference

https://github.com/singulainthony/Cyber-Realistic-Pony-Prompt-Generator — a
ComfyUI node encoding CyberRealistic Pony conventions. Useful for: booster-tag
vocabulary (curated Danbooru-aligned lists for hairstyles, clothing, poses,
locations, lighting, camera angles), its ordering nuance (quality/score/source
→ subject-count tags → location/lighting/camera → subject strings), and
source-bias handling (e.g. photorealistic presets relax anime tags in the
negative). Treat as vocabulary/ordering inspiration for the pony profile's
dialect instructions, not as a dependency.

## Requirements

### R1 — PromptProfile schema

New schema (visual/schema.py area): profile id, distillation dialect
instructions (the text block injected into the distill template), quality/
special-tag prefix, rating-tag convention (pony only), negative-prompt base,
token budget, weight cap, art-style-required flag. Ship the three built-ins
above; structure so a fourth is a data addition, not code.

### R2 — Checkpoint → profile resolution

- Mapping config on the comfyui image actions (where the checkpoint `model`
  config already lives), with name-regex defaults:
  `(?i)pony|score_9` → pony; `(?i)juggernaut|realvis|realistic vision` →
  sdxl_natural; unknown → pony (current behavior, explicit).
- Per-checkpoint override editable in agent config (choices populated the way
  handle_checkpoints already lists them).
- Resolution helper on the visual agent: checkpoint (request extra_config
  override or action config) → profile. Backends with
  prompt_type=DESCRIPTIVE resolve to `descriptive` regardless.

### R3 — Resolve before composition

`GenerationRequest` gains `prompt_profile` (id, default resolved at request
assembly). The distillation path (`_distill_prompt`,
`begin_prompt_distillation` pre-start, and the
`visual.distill-image-prompt` template) receives the profile and writes the
prompt in that dialect:
- pony: the four-section format above (this CHANGES current Pony output too —
  three-score keyword-only prompts become four-score + NL paragraphs +
  booster tags).
- sdxl_natural: component-ordered NL, ≤75-token budget enforced in the
  contract and in `_trim_to_budget`.
The pre-start/consume key (vis_type, character_name, instructions) must add
the profile id, so a checkpoint switch cannot consume a stale-dialect pending
distillation.

### R4 — Style templates per dialect

Visual style templates currently carry tag stanzas (used as the Pony special/
booster material). Add an optional natural-language style field; sdxl_natural
uses it (or a generated sentence from the tags as fallback); pony keeps tags
and satisfies the art-style-required rule from the same data. Missing new
field = current behavior.

### R5 — Adjust & Visualize modal

Switching the checkpoint dropdown to a different-profile checkpoint re-runs
the prompt-only composition (the wsh visualize prompt_only path — cheap, no
image) so the shown prompt matches the model. Same-profile switches leave the
prompt alone. The preview payload already carries vis_type etc.; add the
profile id so the frontend can compare.

### R6 — Legacy keyword pipeline

The legacy (non-distilled) keyword pipeline in `_finalize_prompt` keeps
working for pony/unknown profiles. For sdxl_natural with distillation
disabled, fall back to legacy output as-is (documented limitation) — do not
attempt keyword→NL conversion without an LLM.

## Out of scope

- New backends; workflow/sampler changes (profiles may carry informational
  sampler hints but nothing applies them in this track).
- Per-model LoRA/embedding management.
- Editing existing saved assets' prompts.

## Acceptance criteria

- AC1: PromptProfile schema + three built-ins; unit-tested defaults.
- AC2: checkpoint→profile resolution with regex defaults + config override;
  unit tests for the regexes and precedence (request override > action config).
- AC3: GenerationRequest carries prompt_profile; distill template renders
  dialect blocks (template render tests per profile).
- AC4: pony output contract: all four score tags + one rating tag + art style
  present + NL factual section + booster tags (assert on a stubbed distill
  response contract, not a live LLM).
- AC5: sdxl_natural output ≤75 tokens post-trim; no score_/rating_ tags.
- AC6: pending-distillation key includes profile; mismatch cancels (test).
- AC7: modal checkpoint switch to different profile triggers re-compose
  (frontend change + payload field; verify statically + one live click).
- AC8: existing visual tests pass; legacy path unchanged for pony.

## Key files (traced 2026-08-01)

- `src/talemate/agents/visual/generation.py` — `_distill_prompt` (~712),
  `_consume_pending_distillation` (~664, the match key), `_finalize_prompt`
  (~526), `begin_prompt_distillation` (~606), `_trim_to_budget`.
- `src/talemate/prompts/templates/visual/distill-image-prompt.jinja2` — the
  dialect contract lives here.
- `src/talemate/agents/visual/schema.py` — GenerationRequest,
  AssetAttachmentContext.
- `src/talemate/agents/visual/nodes.py` — GenerationRequestNode (~470),
  SelectBackend, FinalizePrompt (~699).
- `src/talemate/agents/visual/websocket_handler.py` — handle_checkpoints /
  handle_set_checkpoint (checkpoint list + current).
- `src/talemate/agents/visual/modules/wsh-visualize.json` (prompt_only /
  prompt_preview leg), `generate-visual-asset.json`.
- `talemate_frontend/src/components/VisualLibraryGenerate.vue` — checkpoint
  dropdown, prompt fields, preview merge.
- Style templates: `src/talemate/world_state/templates/` visual_style types +
  `apply_styles` in the visual agent.
