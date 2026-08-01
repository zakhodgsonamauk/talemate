# Shot control — specification

## Goal

Concept-art shots (character embedded in the scene) are currently fought, not
chosen: the portrait-shaped character reference at weight 0.8 drags every
generation toward a centered hero pose, the prompt leads with identity, and -
confirmed bug - removing the character reference in the Adjust modal does
nothing because auto-attach re-adds the subject's cover over an empty list
(references.py:383-391, the empty list skips the validation branch).

Three deliverables: a shot-type selector at compose time, per-request
reference weight sliders, and a WYSIWYG contract for the modal's references.

## Requirements

### R1 — WYSIWYG references (bug fix, first)

- `GenerationRequest.auto_references: bool = True`. When False,
  `attach_character_references` performs NO auto-attach: an empty
  `reference_assets` stays empty (validation of a supplied list still runs;
  the wrong-subject fallback attaches nothing when auto_references is False -
  dropping a wrong ref must not resurrect the cover the user removed).
- The Adjust modal's Generate always sends `auto_references: false` - its
  picker displays exactly what generates.
- Routing invariants: background-only still forces IMAGE_EDIT (existing);
  no references at all with auto_references False = TEXT_TO_IMAGE workflow.
- Tests: empty-list-stays-empty, supplied-list-still-validated,
  wrong-subject-dropped-without-fallback, default-True path unchanged.

### R2 — Shot-type selector

- The pre-compose chooser (vis-type dialog in SceneMessages.vue) gains a
  Shot selector: `auto` (default) / `closeup` / `medium` / `wide`.
- Carried on the request (`GenerationRequest.shot_type: str = "auto"`),
  through `openVisualPromptAdjust` and the compose payload (wsh visualize
  needs the field plumbed the same way instructions/character are - flat
  payload key -> GVA -> GenerationRequest node; verify the module input
  exists or add it the way callback was added).
- Distillation template: per-shot block injected next to the dialect block:
  - wide: environment/setting FIRST, subject as "a lone figure, small in
    frame"; vocabulary `wide establishing shot, full body, from a distance,
    cinematic scale, environment focus`; NEGATIVE gains `close-up, portrait,
    looking at viewer, centered composition, face focus`.
  - closeup: subject face/expression first; `close-up, detailed face`;
    NEGATIVE gains `wide shot, full body, distant`.
  - medium: `medium shot, waist up` guidance, lighter touch.
  - auto: no injection (today's behavior).
  Works for BOTH dialects (pony boosters + sdxl sentences) - keep the
  instructions dialect-neutral, the model adapts them to its output format.
- Wide shots default the character reference weight down (see R3, default
  0.45 when shot_type=wide and no explicit slider value) - the reference is
  a portrait and fights wide composition at 0.8.
- Legacy (non-distilled) path: out of scope beyond not crashing.
- The modal shows the chosen shot type (read-only chip or select) so a
  recompose keeps it; changing it in the modal recomposes like a profile
  switch (reuse the existing recompose path) - stretch goal, may ship
  read-only in v1.

### R3 — Reference weight sliders

- Adjust modal: slider under Reference Images (0.1-1.0, step 0.05, default
  0.8) and under Background Reference (0.1-1.0, default 0.45). Only shown
  when the respective reference list is non-empty.
- Sent via `extra_config.character_ref_weight` / `extra_config.bg_ref_weight`
  (extra_config already flows end-to-end and is recorded on the saved
  asset's meta - regenerate reproduces the weights for free).
- Backend: `workflow.set_reference_weights(character, background)` mirroring
  set_clip_skip - finds IPAdapterAdvanced nodes by title ("Apply Character
  Reference" / "Apply Background Reference"), sets `weight`. Missing node =
  silent skip. Applied in comfyui generate from extra_config with the
  R2 wide-shot default when unset.
- Tests: node targeting, defaults, wide-shot default interplay
  (explicit slider beats shot default beats baked value).

## Out of scope

- Per-vis-type baked weight tables (the shot default covers the pain).
- Regional conditioning / masking.
- Shot control for qwen/edit backends beyond not crashing.

## Acceptance criteria

- AC1: modal remove-character-ref + keep-background generates with ONLY the
  background reference (verify via debug log `attach_character_references`
  events + ComfyUI /history: sampler rewired to background chain, character
  IPAdapter image = placeholder/disconnected).
- AC2: shot_type=wide produces a distilled prompt leading with environment,
  containing wide-shot vocabulary, negatives containing close-up terms -
  template render tests per dialect + one live compose.
- AC3: sliders present when refs selected; values land in /history node
  weights; regenerate reproduces them.
- AC4: existing suites green; auto_references default path byte-identical
  behavior (existing tests unchanged).

## Key files

- src/talemate/agents/visual/references.py (attach_character_references)
- src/talemate/agents/visual/schema.py (auto_references, shot_type)
- src/talemate/agents/visual/generation.py (_distill_prompt vars)
- src/talemate/prompts/templates/visual/distill-image-prompt.jinja2
- src/talemate/agents/visual/backends/comfyui.py (set_reference_weights,
  generate hook)
- src/talemate/agents/visual/modules/wsh-visualize.json (shot_type plumb -
  mirror the GET obj.callback pattern added 2026-08-01)
- talemate_frontend/src/components/SceneMessages.vue (chooser selector)
- talemate_frontend/src/components/VisualLibraryGenerate.vue (sliders,
  auto_references: false, shot type display)
- Live verification: scripts/e2e_workflow_check.py pattern, ComfyUI /history.
