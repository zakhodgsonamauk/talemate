# Shot control — review notes

## Phase 1 — WYSIWYG references (94eee32)

Featherweight review 20260801-084309-c123 (qwen3.5:cloud): no issues found.
Verified: early-return ordering (validation before both auto_references gates),
gen_type mutation isolated to the auto-attach block, both internal callers
(generate, FinalizePrompt preview) default True = byte-identical behavior.
Optional hardening suggestion (extra doc note at the empty-assignment) declined —
the surrounding comments already carry the why. Disposition: no changes.

## Phase 2 — shot-type selector (commit "feat(visual): shot-type selector")

Featherweight review 20260801-090031-e3f4 (qwen3.5:cloud): wiring verified
consistent end to end (chooser → wsh-visualize → generate-visual-asset →
GenerationRequest → distill template; preview KV item8 no collision; Stage
4d8ccb0c state_c single-writer; 5-tuple key unpacked nowhere else).
Dispositions:
- "medium missing from SHOT_BLOCKS" — REJECTED, stale read: medium is defined
  and test_medium_shot_block_renders pins it.
- "shotType editable during promptLoading" — non-issue: the modal select
  already carries :disabled="generating || promptLoading".
- "add auto-prestart vs non-auto consume mismatch test" — ACCEPTED:
  test_non_auto_shot_mismatches_the_auto_prestart added (Phase 3 commit).
- "document prestart tradeoff in code" — already present at the key
  construction comment.
