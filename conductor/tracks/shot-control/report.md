# Shot control — track report

**Status**: complete (code); live acceptance pending the user's next generations.
**Commits**: 94eee32 (Phase 1), "feat(visual): shot-type selector" (Phase 2),
7b05cab (Phase 3), close-out (Phase 4 hardening + track docs).

## What shipped

### R1 — WYSIWYG references (the confirmed bug)
`GenerationRequest.auto_references` (default True). When False the caller's
reference list is authoritative: an emptied list stays empty, and dropping a
wrong-subject reference no longer resurrects the subject's cover. The Adjust
modal's prompt-mode Generate sends `auto_references: false`; instruct mode has
no picker and keeps auto-attach. gen_type untouched by the early returns —
no references + auto_references False generates TEXT_TO_IMAGE.

### R2 — Shot-type selector
Chooser (pre-compose dialog) gains Shot: auto / closeup / medium / wide.
Flows as a flat `shot_type` key → wsh-visualize `GET obj.shot_type` →
generate-visual-asset `IN shot_type` → GenerationRequestNode →
`GenerationRequest.shot_type` → SHOT_BLOCKS framing block in the distillation
contract (wide = environment-first, subject "a lone figure, small in frame",
plus close-up negatives; the block is the one sanctioned exception to the
template's no-framing-language rule). FinalizePrompt exposes shot_type into
the prompt_preview payload so the modal's Shot select survives the round trip;
changing it in the modal recomposes like a cross-dialect model switch.
Distillation pending-key grew to a 5-tuple; the prestart hardcodes "auto", so
a non-auto shot trades the parallel prestart for a correct fresh distillation.

### R3 — Reference weight sliders
`Workflow.set_reference_weights(character, background)` sets `weight` on the
IPAdapterAdvanced nodes by title ("Apply Character Reference" / "Apply
Background Reference"); missing node = silent skip. Precedence pinned in
`resolve_reference_weights`: explicit slider (extra_config) > wide-shot
character default 0.45 > workflow baked value. Modal sliders under each
reference picker (0.1–1.0, step 0.05, defaults 0.8/0.45); untouched sliders
follow the shot default so they always show what generates; values ride
extra_config so a regenerate reproduces them from asset meta (clamped on
restore).

## Verification

- Suites: test_visual_reference (26), test_prompt_profiles (29, incl. shot
  render tests both dialects), test_visual_distillation (20, incl. the
  auto-prestart vs wide mismatch), test_background_reference (12, incl.
  weight targeting/precedence) — all green. Full tests/ run: only the known
  pre-existing platform failures (Dropbox rmtree teardown locks in
  world-state template suites, Windows path-sep assert in test_encryption,
  director-template time flakes, cp1252 section-validation reads).
- Graph JSON: minimal diffs (+102/-3), dangling-edge scan clean, fresh uuid4
  ids, Stage state_c slot single-writer, DictCollector item8 collision-free.
- Both SFCs compile clean via vue/compiler-sfc.
- Three featherweight reviews (dispositions in review-notes.md): Phase 1 no
  issues; Phase 2 two stale-read findings rejected with evidence, one test
  suggestion adopted; Phase 3 two hardenings adopted, one declined.

## Live acceptance (read-only, pending)

- AC1: remove character ref + keep background → debug log
  `attach_character_references.explicit_no_refs`, ComfyUI /history shows
  sampler on the background chain only.
- AC2: wide shot → distilled prompt leads with environment, negatives carry
  close-up terms.
- AC3: slider values land in /history node weights; regenerate reproduces.

No backend restart was performed; Python/module-JSON changes require one
(user-coordinated) before live behavior reflects this track. Frontend
changes hot-reload.
