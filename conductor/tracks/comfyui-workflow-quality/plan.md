# ComfyUI workflow quality — implementation plan

**Spec**: `conductor/tracks/comfyui-workflow-quality/spec.md` (read fully).

Standing constraints: user's backend/browser may be active - static
verification plus the proven live patterns (ComfyUI /history diffing, the
frontend-slot-race websocket harness in scripts/e2e_profile_flow.py) only
when a backend bounce is coordinated or authorized. Commit per phase,
conventional messages, footer `Co-Authored-By: Claude Fable 5
<noreply@anthropic.com>`. Featherweight second-opinion review per phase;
dispositions in review-notes.md. Frontend is a hot-reloading dev server.

## Phase 1 — Model carry-through (R4; smallest, user-facing bug)

### [ ] T1 — Chooser checkpoint into the modal
SceneMessages.vue `confirmVisType`: include the chosen checkpoint in the
request passed to `openVisualPromptAdjust` (as `extra_config: {checkpoint}`).
VisualLibraryGenerate.vue `applyInitialRequest` already reads
`r.extra_config.checkpoint` into the dropdown with `checkpointFromRequest`
(regenerate path) - verify it fires for this shape, and that the later
`checkpoints` response does not clobber (existing guard). Confirm Generate
sends it per-request. Manual click-through + note.

**Commit + featherweight review.**

## Phase 2 — CLIP skip + sampler rebake (R1, R2)

### [ ] T2 — CLIPSetLastLayer nodes in workflows
Add "Talemate CLIP Skip" (`CLIPSetLastLayer`, `stop_at_clip_layer: -1`)
between checkpoint CLIP and both text encoders in default-sdxl,
sdxl-ipadapter-character, sdxl-ipadapter-character-multi (+inpaint if it has
text encoders off the main CLIP). Script the JSON edit (uuid ids, rewire the
two `clip` inputs). Validate: json.load + every text encoder's clip input
sources the new node.

### [ ] T3 — PromptProfile.clip_skip + backend application
schema.py: `clip_skip` field (pony -2, others -1). comfyui.py: mirror the
`workflow.set_sampler` pattern - `workflow.set_clip_skip(value)` keyed on the
node title, applied in the generate path from
`get_prompt_profile(request.prompt_profile).clip_skip`; missing node =
debug-log skip. Unit test with a loaded workflow object.

### [ ] T4 — Rebake sampler defaults
ipadapter-character(+multi): cfg 8 -> 6.0 (match MODEL_PROFILES pony).
default-sdxl: 50/8/dpmpp_2m_sde -> 30/6/dpmpp_2m karras. json.load validate.

**Commit + featherweight review.**

## Phase 3 — Background reference (R3)

### [ ] T5 — Capability check + workflow chain
Query ComfyUI /object_info for IPAdapterAdvanced weight_type choices (live,
read-only - no coordination needed). Then script-edit
sdxl-ipadapter-character(+multi): LoadImage "Talemate Background Reference"
(placeholder.png) -> shared CLIPVisionLoader reuse -> IPAdapterModelLoader
(ip-adapter-plus_sdxl_vit-h) -> IPAdapterAdvanced "Apply Background
Reference" (weight 0.45, weight_type "style transfer" or fallback per spec,
end_at 0.8), chained after the character IPAdapter model output into the
KSampler model input. CRITICAL: when no background reference is supplied the
chain must be inert - either the backend bypasses the node (rewire model edge
per request, mirroring how references route requests today) or weight 0 is
set; decide by reading how set_reference_images handles absent refs.

### [ ] T6 — Typed slots + backend routing
schema.py `background_reference_assets`; comfyui backend: upload + assign to
"Talemate Background Reference" nodes (mirror set_reference_images);
references.py: leave subject logic untouched, but ensure
attach_character_references ignores/preserves background refs (it validates
`reference_assets` only). SelectBackend/edit-routing: background refs alone
also route to the ipadapter workflow.

### [ ] T7 — Modal picker
VisualLibraryGenerate.vue: second VisualReferenceImages block ("Background
reference", max 1), bound to `backgroundReferenceAssets`, sent on Generate.
No auto-attach.

**Commit + featherweight review.**

## Phase 4 — Verify + close (AC1-AC5)

### [ ] T8 — Live verification + report
- pytest visual suites.
- Live (coordinate backend bounce or use the slot-race harness): one pony
  generation -> /history shows clip_skip -2 node + background reference
  filename when supplied; one generation without background ref succeeds.
- Chooser->modal click-through statement (or user checklist item).
- report.md with AC table + the Option B/C follow-up sketch + location-
  canonical-image design note. tracks.md + metadata. Final featherweight
  review. Push.
