# Review notes — model-aware-prompting

## Whole-track featherweight review (job 20260801-014442-d0f7)

- "BLOCKING": single sentence over budget kept untrimmed — DISPUTED as designed
  behavior: the reviewer's own suggested fix produces identical output (first
  sentence always kept). A slightly over-budget prompt that depicts the subject
  beats an empty one. Intent now documented in the trim loop.
- NON-BLOCKING punctuation ("She runs!." class): fixed — terminal punctuation
  preserved, period added only when absent.
- NON-BLOCKING `_tagless` matching standalone "booru": kept — the literal
  "booru tags" phrase was observed emitted by the live model; no legitimate
  style keyword starts with "booru".
- NON-BLOCKING older-backend empty profiles map: not applicable — frontend and
  backend ship from the same repo; noted for anyone running mismatched builds.
- Verified clean: no backend=None crash paths, precedence order correct,
  stale 3-tuple pending keys cancel safely, no frontend recompose loops.

## Live testing (overnight 2026-08-01)

- scripts/profile_dialect_check.py: real template x real distillation model
  (glm-5.2:cloud) — pony four-section contract and sdxl_natural contract both
  PASS (after strengthening the sdxl cap wording to sentences/words and adding
  the deterministic sentence trim).
- scripts/e2e_profile_flow.py: full backend E2E over websocket (own session,
  base scene) — checkpoint profile map served, set_checkpoint x2, prompt_only
  compose per checkpoint, preview payload carries prompt_profile, pony prompt
  had all four score tags + NL description, sdxl prompt had no pony tags and
  landed at ~75 CLIP tokens. PASS; user's checkpoint restored.
