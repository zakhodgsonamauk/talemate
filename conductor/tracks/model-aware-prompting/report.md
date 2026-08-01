# Model-aware prompt generation — execution report

Executed overnight 2026-08-01, directly in the main session, with live testing
against the running stack (per the user's "test it properly" mandate).

## Commits

| Commit | Content |
|--------|---------|
| d71d158 | PromptProfile schema + three built-ins; checkpoint->profile resolution (regex defaults, JSON override config, precedence) + 15 tests |
| 7bd4e79 | Dialect-aware distillation (per-profile template contracts, profile-keyed pending distillation, profile-aware style merge, legacy guard); checkpoints response profile map; preview payload prompt_profile; modal recompose on cross-profile switch |
| b717238 | Live-validation fixes (sentence-based cap wording + deterministic trim, visual-only NEGATIVE), test harnesses, review polish |

## Acceptance criteria

- AC1 profiles + built-ins: DONE (pony incl. score_6_up, sdxl_natural 75-token
  budget, descriptive; tested).
- AC2 resolution: DONE (patterns pony/cyberrealistic + juggernaut/realvis,
  request > config precedence, checkpoint_profiles JSON override; tested).
- AC3 request carries profile, dialect template renders: DONE (render tests).
- AC4 pony contract: DONE - verified LIVE against glm-5.2:cloud: four score
  tags lead, NL Five W's description, stylistic phrase, booster tags with sex
  tag + exactly one rating tag.
- AC5 sdxl contract: DONE - verified LIVE: no score/rating tags, natural
  sentences, ~75 CLIP tokens post-trim, subject leads.
- AC6 pending key includes profile: DONE (mismatch cancels; stale 3-tuple keys
  from old builds also mismatch safely - reviewer-verified).
- AC7 modal recompose: frontend + payload shipped; websocket-level behavior
  E2E-verified (profile map + set_checkpoint + recompose produce the right
  dialect); the click-path itself needs one human confirmation.
- AC8 suites: 350 visual/profile tests green; pre-existing failures unchanged.

## Live testing evidence

- `scripts/profile_dialect_check.py` - renders the real template, calls the
  real distillation model, asserts the dialect contract. PASS both profiles.
- `scripts/e2e_profile_flow.py` - full websocket E2E on the running backend
  (own session + base scene, user's checkpoint restored afterwards). PASS:
  profile map served, both checkpoints composed in their own dialect,
  prompt_preview carried prompt_profile.
- Both harnesses left in scripts/ for re-running after future changes.

## User verification (morning checklist)

1. Refresh browser (dev server hot-reloaded the modal changes).
2. Adjust & Visualize with a Pony checkpoint: prompt should now lead with all
   FOUR score tags and read as structured description + booster tags, not a
   bare keyword list.
3. Switch the modal's Image Model to Juggernaut XI: a "Recomposing..." notice
   should appear and the prompt should re-arrive as short natural language
   with no score tags.
4. Generate on each - the visible quality difference on Juggernaut is the
   point of the whole track.
5. Optional knob: Visualizer agent -> ComfyUI -> "Checkpoint prompt profiles"
   JSON for any custom checkpoint the name patterns misclassify.
