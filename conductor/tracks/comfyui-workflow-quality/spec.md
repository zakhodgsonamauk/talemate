# ComfyUI workflow quality — specification

## Goal

The prompt-side dialect work (model-aware-prompting) is done; the workflow side
has three gaps against model guidance, plus one UX defect in the new
model-picker flow. Audit findings (2026-08-01, workflows read in full):

1. **No CLIP skip anywhere.** Pony-family checkpoints are trained at clip
   skip 2; every Pony generation to date ran at the default encoding depth -
   a standing quality tax. No `CLIPSetLastLayer` node exists in any workflow.
2. **Baked workflow defaults contradict the sampler profile table.**
   `sdxl-ipadapter-character.json` bakes CFG 8 for CyberRealisticPony while
   `MODEL_PROFILES` (backends/comfyui.py:62) says CFG 6.0 - and profiles apply
   only when the request OVERRIDES the checkpoint, so the workflow's own
   default model runs the wrong CFG. `default-sdxl.json` bakes 50 steps /
   CFG 8 / dpmpp_2m_sde for a checkpoint not in use.
3. **No background/environment reference.** References condition subject
   identity only (`ip-adapter-plus-face`); there is no way to hand the
   generation a reference image for the setting/mood.
4. **Chooser-selected model must carry into the Adjust modal
   deterministically.** The vis-type chooser now sets the model via
   set_checkpoint before composing; the modal then learns the current model
   from its own `checkpoints` round-trip. That is race-prone and the modal's
   dropdown can briefly (or, mis-ordered, persistently) show the old model.

## Requirements

### R1 — Per-profile CLIP skip

- Add a `CLIPSetLastLayer` node ("Talemate CLIP Skip") between the checkpoint
  loader's CLIP output and both CLIPTextEncode nodes in `default-sdxl.json`,
  `sdxl-ipadapter-character.json`, and `sdxl-ipadapter-character-multi.json`
  (inspect `sdxl-ipadapter-inpaint.json` too). Default `stop_at_clip_layer: -1`
  (no-op).
- `PromptProfile` gains `clip_skip: int` (pony: -2, sdxl_natural: -1,
  descriptive: -1). The backend applies it per generation the same way
  `workflow.set_sampler` applies sampler profiles - keyed on the node title,
  silently skipped when the workflow lacks the node.
- The profile is already resolved on the request (`prompt_profile`), so the
  backend needs no new resolution logic.

### R2 — Align baked sampler defaults with the profile table

- Update the baked steps/cfg/sampler in the SDXL workflows to match
  `MODEL_PROFILES` for their baked checkpoints (ipadapter-character: CFG 8 ->
  6.0, steps 30, dpmpp_2m karras stays).
- `default-sdxl.json`: rebake to a sane generic SDXL profile (30 steps,
  CFG 6, dpmpp_2m karras) rather than 50/8/sde.
- Do NOT change the profiles-apply-only-on-override rule in
  `resolve_checkpoint` - the rebake makes baked and table agree, which is the
  actual invariant worth having.

### R3 — Background reference (style-transfer IPAdapter)

- New chain in `sdxl-ipadapter-character.json` (and -multi): LoadImage
  ("Talemate Background Reference") -> IPAdapterModelLoader
  (`ip-adapter-plus_sdxl_vit-h.safetensors` - the NON-face model) ->
  IPAdapterAdvanced ("Apply Background Reference", weight ~0.45,
  `weight_type: "style transfer"`, end_at 0.8) chained after the character
  IPAdapter's model output. Style transfer moves palette/lighting/mood/
  environment without transferring subject identity.
- Verify the installed IPAdapter Plus extension supports
  `weight_type: style transfer` via /object_info before relying on it; if the
  installed version predates it, fall back to `weight: 0.3, weight_type:
  linear` and note it.
- Schema: `GenerationRequest.background_reference_assets: list[str]` (typed
  separately from subject references - they route to different workflow
  slots). Backend: reference upload/set mirrors the existing
  `set_reference_images` path but targets "Talemate Background Reference"
  titled nodes; absent node = silently skip.
- Selection: v1 is user-driven only - a "Background reference" picker in the
  Adjust modal (same VisualReferenceImages component, separate list, filtered
  to non-character assets first). NO automatic attachment in v1 (the
  auto-attach heuristics burned us before; see references.py history).
- When only a background reference is present (no subject reference), the
  request still routes to the edit backend/ipadapter workflow.

### R4 — Deterministic model carry-through (chooser -> modal)

- The vis-type chooser passes its chosen checkpoint INTO
  `openVisualPromptAdjust` (request field, e.g. `extra_config.checkpoint`)
  so `applyInitialRequest` sets the modal's dropdown immediately
  (`checkpointFromRequest = true` semantics already exist for regenerate).
- The modal must not clobber it when its own `checkpoints` response arrives
  (existing `checkpointFromRequest` guard covers this - verify).
- Generate from the modal already sends `extra_config.checkpoint`; confirm
  the chooser-chosen model therefore rides the request end-to-end even if the
  global set_checkpoint were to fail.

## Out of scope

- Regional/attention-masked IPAdapters (Option B) and depth ControlNet
  location continuity (Option C) - follow-up tracks.
- Automatic location-canonical-image attachment (mirror of character covers) -
  design sketch belongs in the report, not the build.
- qwen/z-image/sd15 workflows.

## Acceptance criteria

- AC1: pony generations run with clip skip 2 (verify via ComfyUI /history:
  the submitted graph contains the node with -2); sdxl_natural runs -1.
- AC2: baked workflow sampler values equal MODEL_PROFILES for their baked
  checkpoints.
- AC3: a background reference image reaches the "Talemate Background
  Reference" node (verify via /history), generation succeeds, and a
  no-background-reference request still works (node fed placeholder, weight
  path inert or bypassed).
- AC4: chooser-chosen model appears as the modal dropdown's value on open,
  no round-trip race; Generate records it in the asset's extra_config.
- AC5: existing visual suites green; live E2E harness extended or rerun.

## Key files

- `templates/comfyui-workflows/*.json` (workflow graphs)
- `src/talemate/agents/visual/backends/comfyui.py` (set_sampler pattern to
  mirror for clip skip + background refs; /object_info capability check)
- `src/talemate/agents/visual/schema.py` (PromptProfile.clip_skip,
  GenerationRequest.background_reference_assets)
- `src/talemate/agents/visual/references.py` (typed slots note)
- `talemate_frontend/src/components/SceneMessages.vue` (chooser carry-through)
- `talemate_frontend/src/components/VisualLibraryGenerate.vue` (modal dropdown
  init, background reference picker)
- Live verification: scripts/e2e_profile_flow.py pattern + ComfyUI /history
  diffing (proven tonight).
