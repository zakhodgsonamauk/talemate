# Shot control — implementation plan

**Spec**: `conductor/tracks/shot-control/spec.md` (read fully - it carries the
confirmed bug trace and the exact plumb patterns to mirror).

Standing constraints: NEVER stop/start/restart the backend or Ollama without
the user's explicit go - coordinate restarts as a named step and wait.
Frontend dev server hot-reloads .vue. Static verification + read-only live
checks (debug log, ComfyUI /history) are always allowed. Commit per phase,
footer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`. Featherweight
second-opinion review per phase; dispositions in review-notes.md.

## Phase 1 — WYSIWYG references (R1; the bug, smallest)

### [ ] T1 — auto_references field + attach guard
schema.py: `auto_references: bool = True` on GenerationRequest.
references.py attach_character_references: when False - validate any supplied
list as today, but on empty/kept-empty result RETURN without the subject
fallback (and without touching gen_type; the background-only IMAGE_EDIT
promotion in generate() is unaffected). Update the wrong-subject fallback:
only refill from the subject's cards when auto_references is True.
Tests in tests/test_visual_reference.py: four cases per spec R1.

### [ ] T2 — modal sends auto_references: false
VisualLibraryGenerate.vue Generate payload (prompt mode AND instruct mode
paths). Manual click-through note.

**Commit + featherweight review.**

## Phase 2 — Shot type (R2)

### [ ] T3 — schema + chooser + plumb
- schema.py: `shot_type: str = "auto"` on GenerationRequest.
- SceneMessages.vue vis-type dialog: Shot select (Auto/Close-up/Medium/Wide)
  under the model select; carried via the request into
  openVisualPromptAdjust and the compose payload (flat `shot_type` key).
- wsh-visualize.json: `GET obj.shot_type` -> GVA input (add module Input node
  + SET local + GET -> GenerationRequest node input `shot_type`; mirror the
  callback plumb from 2026-08-01 - fresh uuid4 ids, json.load + structural
  assertions after).
- GenerationRequestNode (nodes.py): accept + pass shot_type.

### [ ] T4 — distillation shot blocks
distill-image-prompt.jinja2: `shot` var -> per-shot instruction block next to
the dialect block (dialect-neutral wording per spec). generation.py
_distill_prompt: pass shot_type + a SHOT_BLOCKS mapping (schema or
generation constants). Render tests: wide/closeup/auto x both dialects.

### [ ] T5 — modal shot display + recompose
VisualLibraryGenerate.vue: show the shot type; changing it re-sends the
prompt_only compose (reuse recomposeForProfile's payload builder, renamed to
a generic recompose). Ship read-only display if the recompose wiring turns
out to conflict with the profile recompose - note honestly.

**Commit + featherweight review.**

## Phase 3 — Reference weights (R3)

### [ ] T6 — backend set_reference_weights
comfyui.py Workflow method (mirror set_clip_skip): set `weight` on
IPAdapterAdvanced nodes by title. generate(): read
extra_config.character_ref_weight / bg_ref_weight; when unset and
request.shot_type == "wide", character weight defaults 0.45. Tests.

### [ ] T7 — modal sliders
VisualLibraryGenerate.vue: v-slider under each reference card (visible when
list non-empty), bound into extra_config on Generate. Defaults 0.8 / 0.45;
wide shot hint shown when shot_type == wide.

**Commit + featherweight review.**

## Phase 4 — Verify + close

### [ ] T8 — verification + report
- pytest visual suites + render tests.
- Live read-only: after the user's next generations, verify AC1 (log +
  /history: background-only), AC2 (wide prompt), AC3 (weights in /history).
  A coordinated scripted run (scripts/e2e_workflow_check.py pattern) only
  with the user's explicit restart window.
- report.md, review-notes.md, metadata, tracks.md, final review, push.
