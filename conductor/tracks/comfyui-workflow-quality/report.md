# ComfyUI workflow quality — execution report

Executed 2026-08-01, directly in the main session.

## Commits

| Commit | Content |
|--------|---------|
| 71599c9 | Chooser-selected model carries into the Adjust modal (extra_config.checkpoint, race-free) |
| a043128 | CLIPSetLastLayer in all SDXL workflows + PromptProfile.clip_skip (pony -2) applied per generation; baked sampler values rebaked to MODEL_PROFILES (CFG 8->6, default-sdxl 50/8/SDE->30/6/2M-Karras) |
| cea0802 | Background reference: style-transfer IPAdapter chain (non-face model, weight 0.45), typed background_reference_assets, backend upload + sampler rewire (inert by default), modal picker |
| 9ef4b00 | Upload New in the reference picker + scene_asset_uploaded ack; Configure-button layout fix |

## Acceptance criteria

- AC1 (clip skip): code + unit-tested (workflow graphs assert the node; backend
  applies profile clip_skip). LIVE /history check pending user's next
  generation - the scripted run was aborted (see below).
- AC2 (sampler rebake): DONE, asserted in tests.
- AC3 (background reference): chain + routing + picker DONE, unit-tested
  (inert-by-default topology, rewire-on-supply, non-face model, style
  transfer capability confirmed via /object_info). LIVE check pending.
- AC4 (model carry-through): DONE (existing checkpointFromRequest path).
- AC5 (suites): 27 + 331 visual tests green.

## Live verification status

The scripted E2E (scripts/e2e_workflow_check.py) was aborted mid-run - the
backend was bounced underneath its live connection (sequencing error in the
harness orchestration, and it interrupted the user's morning session; process
control now stays with the user). The harness is kept and runnable when a
coordinated window exists. Interim verification is user-driven: after any
generation, read-only checks against logs/talemate-debug.jsonl and ComfyUI
/history confirm clip_skip -2, CFG 6.0, background chain rewire, and verbatim
prompt delivery. The prompt-delivery + ComfyUI-coping checks were already
proven live during the model-aware-prompting track (byte-exact 542/542 and a
905-char direct submission).

## User checklist

1. Adjust & Visualize on a Pony model -> Generate.
2. Optionally: Background Reference -> Configure -> Upload New -> Generate.
3. Ask for the read-only /history verification.

## Follow-up sketches (out of scope, designed)

- Regional/attention-masked IPAdapters for strict subject/background split.
- Depth ControlNet location continuity ("same corridor, same angle").
- Location canonical images auto-attached as background refs (mirror of
  character covers in references.py).
