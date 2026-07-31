# Model-aware prompt generation — implementation plan

**Spec**: `conductor/tracks/model-aware-prompting/spec.md` (read fully; it
carries the dialect ground truth verbatim from the user's guides and the
traced file map — do not re-derive either).

Session constraints (standing for this repo):
- The user's backend may be running mid-session: static verification only
  (`.venv/Scripts/python.exe` pytest/imports, json/jinja parse) unless the
  user hands over restart control. Frontend is a hot-reloading dev server.
- Commit per phase, conventional messages, footer
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`. The
  code-review-graph post-commit hook's UnicodeEncodeError is cosmetic.
- After each phase: fast second-opinion review
  (`mcp__second-opinion__start_featherweight_review`), blocking findings
  fixed before proceeding, dispositions in `review-notes.md`.
- Known-unrelated failures: time-tick chat-sort flakes, cp1252
  template-section validator.
- The debug log (`logs/talemate-debug.jsonl`, `docs/fork/debug-logging.md`)
  is the diagnostic tool: `distill_prompt` events carry subject/tokens/prompt,
  `llm.call.completed` carries attribution, flows are `visual:visualize:*`.

## Phase 1 — Profile schema + resolution (R1, R2)

### [ ] T1 — PromptProfile schema + built-ins
`src/talemate/agents/visual/schema.py`: `PromptProfile` pydantic model +
`PROMPT_PROFILES` registry dict with `pony`, `sdxl_natural`, `descriptive`
per the spec's dialect sections (dialect_instructions as template-injectable
text blocks; keep them faithful to the guide text in the spec). Unit tests:
built-ins exist, pony prefix contains all four score tags, sdxl budget 75.

### [ ] T2 — Checkpoint→profile resolution
Visual agent (generation.py or a new small mixin file): 
`resolve_prompt_profile(request) -> PromptProfile` implementing precedence
(request extra_config checkpoint > action config model) + name-regex defaults
+ per-checkpoint override config on the comfyui image actions (follow how the
`model` config is declared; choices via the existing checkpoint listing).
DESCRIPTIVE backends → descriptive. Tests: regex table, precedence, override.

**Commit + featherweight review.**

## Phase 2 — Profile into the request + distillation (R3)

### [ ] T3 — GenerationRequest.prompt_profile
Schema field (str id, default ""); resolved and set at request assembly:
`GenerationRequestNode.run` (nodes.py ~470) and any python constructor path
used by wsh flows (grep `GenerationRequest(` in agents/visual). The profile
must be resolved BEFORE distillation runs.

### [ ] T4 — Dialect-aware distillation
- `distill-image-prompt.jinja2`: profile blocks — pony renders the
  four-section contract (all four score tags + rating + NL factual Five W's
  + stylistic-with-required-art-style + booster tags); sdxl_natural renders
  the component-ordered ≤75-token NL contract (first sentence = subject
  foundation, trigger words, no score/rating tags); response contract stays
  two-line (positive/negative) as today.
- `_distill_prompt` + `begin_prompt_distillation`: pass the profile; extend
  the pending-distillation match key with profile id
  (`_consume_pending_distillation` ~664).
- `_trim_to_budget`: budget from profile (75 for sdxl_natural; pony keeps
  the current larger budget).
- Negative prompt base from profile.
Tests: template renders per profile with expected markers; pending-key
mismatch on profile change; trim budget selection.

### [ ] T5 — Legacy path guard (R6)
`_finalize_prompt` legacy keyword surgery: unchanged for pony/descriptive;
for sdxl_natural without distillation, skip the score/rating tag additions
(`_add_rating_tags`, `_drop_score_tags_when_dressed` family) that would
inject Pony conventions. Test: legacy path with sdxl_natural profile emits no
score_ tags.

**Commit + featherweight review.**

## Phase 3 — Styles + modal (R4, R5)

### [ ] T6 — Style template natural-language field
Locate the visual_style template schema (world_state/templates) and
`apply_styles` in the visual agent. Add optional `natural_style` text field;
sdxl_natural prefers it, falls back to joining the tag stanza into a style
sentence; pony behavior unchanged (and satisfies art-style-required from the
same data). Test: apply_styles output per profile.

### [ ] T7 — Modal re-compose on profile switch
- Preview payload (wsh-visualize prompt_preview leg + FinalizePrompt outputs)
  gains `prompt_profile`.
- `VisualLibraryGenerate.vue`: on checkpoint change, resolve the new
  checkpoint's profile (needs the checkpoint→profile map exposed — extend the
  `checkpoints` websocket response with per-checkpoint profile ids); if it
  differs from the previewed profile, re-send the prompt_only visualize
  request (the existing sendVisualizeWithPrompt path) and show the composing
  state.
- Same-profile switches: no re-compose.
Static verification + note for one live click in the report.

**Commit + featherweight review.**

## Phase 4 — Close

### [ ] T8 — Verification + report
- `.venv/Scripts/python.exe -m pytest tests/ -q -k "visual or prompt"` plus
  full jinja/json parse of touched templates/graphs.
- report.md: AC checklist; live-test script for the user (needs backend
  restart): (1) checkpoint=CyberRealistic Pony → Adjust & Visualize → prompt
  shows 4-section Pony format incl. score_6_up + art style; (2) checkpoint=
  Juggernaut XI → same paragraph → prompt is NL ≤75 tokens, no score tags;
  (3) switch checkpoint in the open modal across profiles → prompt
  re-composes. Debug-log greps for `distill_prompt` events included.
- tracks.md + metadata update. Final whole-track featherweight review.

## DAG
T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 (T5 parallel-safe with T4 reviewwise;
T6 independent of T7 until the payload field).
