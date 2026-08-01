# Shot control — review notes

## Phase 1 — WYSIWYG references (94eee32)

Featherweight review 20260801-084309-c123 (qwen3.5:cloud): no issues found.
Verified: early-return ordering (validation before both auto_references gates),
gen_type mutation isolated to the auto-attach block, both internal callers
(generate, FinalizePrompt preview) default True = byte-identical behavior.
Optional hardening suggestion (extra doc note at the empty-assignment) declined —
the surrounding comments already carry the why. Disposition: no changes.
