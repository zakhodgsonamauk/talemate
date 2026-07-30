# Track: visual-anchor-freshness

**Type**: feature
**Created**: 2026-07-30
**Design doc**: `docs/fork/visual-anchor-freshness-design.md` (read it — this spec does
not repeat it)
**Follows**: `visual-consistency`

## Problem

Visual anchors are derived once and cached forever; nothing invalidates them. Three
failures, evidenced in the design doc:

- **P1** clothing was baked into the identity anchor, so it contradicts the scene. A live
  prompt contained both `fitted dark blue-grey utility suit` and `naked`.
- **P2** editing `base_attributes["appearance"]` does not invalidate the anchor, so it
  goes stale silently and permanently.
- **P3** the scene anchor is derived from the premise, so `starship interior` persists
  after the party leaves the ship.

## Goal

Appearance tracks the story automatically, while identity stays pinned. Specifically:
clothing and physical state refresh as the story advances; species, skin, markings and
build do not drift; the setting follows the party.

## Acceptance criteria

See the design doc's AC1-AC9. Summarised: no wardrobe contradiction; identity byte-stable
across a wardrobe change; wardrobe follows the story with no manual step; appearance edit
invalidates; location switch switches anchors; revisiting a location makes no derivation
call; cached clothing is stripped once on load; a hedged permanent-change answer never
clears an anchor; verified live through Playwright.

## Out of scope

- Reference-image consistency and per-character LoRA (still deferred).
- Rendering fidelity — correct prompts are not a checkpoint honouring them.
- Any change to how identity anchors are *derived*, beyond removing clothing from the
  template.

## Technical notes

- **Lean on existing cadence machinery, do not write detectors.** `Reinforcement`
  (`question`/`answer`/`interval`/`due`) is already refreshed by
  `auto_update_reinforcments` (`agents/world_state/reinforcements.py:64-93`).
  `world_state.location` is already maintained by the world-state snapshot
  (`agents/world_state/snapshot.py:562-563`).
- **`scene.environment` is not the location.** It holds `"scene"` or `"creative"` — a
  mode.
- **Conflict resolution is the hard part, not the plumbing.** Wardrobe is a fallback that
  must be suppressed when the scene already speaks to clothing or state. First-occurrence
  dedupe cannot resolve `naked` versus `utility suit`; they are a contradiction, not a
  duplicate. This lands in `_finalize_prompt`, next to the existing in-frame pruning.
- **The permanent-change reinforcement is the risky piece.** An LLM asked "did anything
  change?" will sometimes oblige. Require a specific assertion, log every invalidation,
  and rate-limit them. If it proves noisy it degrades to manual without taking the rest
  of the track with it.
- **Cost is real.** Two reinforcements per character means eight extra periodic LLM
  queries on a four-character scene. Both intervals configurable, whole feature
  switchable off.
