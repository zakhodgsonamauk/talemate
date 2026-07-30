# Track: visual-consistency

**Type**: feature
**Created**: 2026-07-30
**Design doc**: `docs/fork/visual-consistency-design.md` (read it — this spec does not
repeat it)

## Problem

Generated `SCENE_ILLUSTRATION` images show a different person every time. The setting
drifts too — one sampled generation put mountains outside a starship control room. A
third of the prompt tokens carry no visual meaning.

Four stacked root causes, all evidenced in the design doc:

- **RC1** — no identity anchor. Canonical appearance exists in
  `base_attributes.appearance` but the template re-asks the LLM for it every
  generation via `batch_query_scene`. A non-deterministic source cannot produce a
  consistent subject.
- **RC2** — keyword compression drops the world. Zero setting tokens survived in the
  sampled prompt; plot words (`corruption`, `diagnostic`, `waiting`) did.
- **RC3** — template meta-instructions leak into the image prompt (`horizontal`,
  `landscape`, `cinematic`, `dynamic`, `action`, `interaction`). Format is already
  fixed by resolution.
- **RC4** — `visual_rules` is silently dropped. Elmer's HARD face-in-shadow rule never
  reaches the prompt.

## Goal

The same character is recognisably the same character across illustrations in a scene,
the setting is named in every prompt, and prompt tokens are spent on things a
diffusion model can actually render.

## Acceptance criteria

Verified end-to-end through Playwright against the running app on `localhost:8082`,
review queue at
`#/s/infinity-quest-dynamic-story-v2/main?save=Infinity+Quest+1.json&visual=review`.

1. **AC1 — Identity is byte-stable.** Two consecutive `SCENE_ILLUSTRATION` generations
   in the same scene produce prompts whose character-anchor substring is
   byte-identical.
2. **AC2 — Setting present.** Every prompt contains scene-anchor tokens naming the
   location type and genre. The mountains failure cannot recur.
3. **AC3 — No meta tokens.** No D4 blocklist entry appears in any final prompt.
4. **AC4 — `visual_rules` honoured.** Elmer's face-in-shadow rule appears in every
   prompt where Elmer is in frame.
5. **AC5 — Off-screen characters excluded.** A character absent from the descriptive
   text contributes no anchor.
6. **AC6 — Budget respected.** Assembled positive prompt stays within the D6 cap;
   truncation drops LLM action keywords first, then extra character anchors, then the
   scene anchor. Styles and the first character anchor are never truncated.
7. **AC7 — UI round-trip.** Edit an anchor in the world editor, save, reload the page,
   the value persists, and the next generation uses the edited value.
8. **AC8 — Seed.** With `seed_mode: scene`, two generations report the same seed in
   asset metadata; with `random`, they differ.
9. **AC9 — Playwright evidence.** Generate, open the review queue, screenshot, and
   assert on the visible Prompt text for AC2, AC3, AC4.

## Out of scope

- Reference-image consistency (IP-Adapter, ControlNet, `IMAGE_EDIT` with the character
  card as reference). Strongest available lever, deliberately deferred —
  `backend_image_edit` is empty in `config.yaml` and it needs a second backend running.
- Per-character LoRA training.
- Upstream-merge-friendly file layout. Changes go in place by explicit decision.
- Moving the repo off the `zakhodgsonamauk` account.
- Fixing or deleting the broken `StyleMixin.apply_style` (singular). Documented as a
  hazard; optional cleanup, not this track.

## Technical notes

- **Single choke point.** `StyleMixin.apply_styles` (`style.py:160`) runs *after* the
  LLM's parts are already in `prompt.parts` (called from `nodes.py:347-350`). Anchor
  insertion, in-frame detection, and sanitising all fit there. The 140-node graph
  `agents/visual/modules/generate-visual-asset.json` stays untouched.
- **Insertion order is load-bearing.** `_build_prompt` dedupes with `dict.fromkeys`
  (`schema.py:168`), first occurrence wins. Anchors must precede the LLM part.
- **`prompt_generation.max_length`** (`agent.py:167`) is the LLM's generation budget,
  not an image-prompt cap. D6 adds a separate cap; do not overload the existing one.
- **Precedent to copy for both new fields**: `visual_rules` —
  `character.py:48`, `server/world_state_manager/character.py:121`,
  `WorldStateManagerCharacterVisualsRules.vue`.
- **D5 removes LLM calls.** Dropping the per-character appearance RAG queries makes
  generation faster, not slower.
