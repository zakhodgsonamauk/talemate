# Visual anchor freshness — design

**Status**: design agreed, not implemented
**Date**: 2026-07-30
**Follows**: `docs/fork/visual-consistency-design.md` (read that first — this assumes
anchors exist and are cached)

## Problem

Anchors are derived once and cached forever. Nothing invalidates them. Three distinct
failures fall out of that, in increasing order of how wrong they are.

### P1 — clothing is baked into the permanent layer

The derivation template asks for "the clothing they habitually wear", so wardrobe ended
up inside the identity anchor. It then contradicts the scene. One prompt from a live
generation, verbatim:

```
standard-issue black EVA suit with silver rank markings on collar   <- Elmer's anchor
fitted dark blue-grey utility suit, tool loops and pockets on belt  <- Kaira's anchor
naked                                                              <- what the scene said
```

The model split the difference and produced a tank top. This is the worst of the three,
because it makes the anchor actively harmful rather than merely stale.

### P2 — editing appearance does not invalidate the anchor

`WorldStateManager.update_character_attribute` (`manager.py:334-346`) writes the
attribute and nothing else. Rewrite a character's `appearance` and every future image
keeps the old anchor, silently and indefinitely.

### P3 — the scene anchor is premise-level, not location-level

`Scene.visual_anchor` is derived once from `scene.description`, which is the story's
premise. It currently reads `starship interior, deep space, science fiction, ...`. Move
the party onto the derelict structure and every image still insists on a starship
interior.

## Why not simply re-derive as the story moves

Because that is the bug the previous track removed. Re-deriving identity from a
non-deterministic source on a cadence produces a different person per image, just on a
slower clock. Identity must stay pinned.

The resolution is that two different kinds of visual fact were conflated:

| Layer | Contents | Lifetime | Source |
|---|---|---|---|
| Identity | species, skin, markings, eye structure, hair colour, build, anatomy | permanent | derived once, cached |
| State | clothing, injuries, dirt, wet, what is on or off | changes constantly | refreshed on a cadence |

Both belong in the prompt. Only one of them should be stable.

## Design

Everything below leans on machinery that already exists and already runs on a cadence.
No new change-detectors.

### D1 — Identity anchor becomes identity-only

`derive-visual-anchor.jinja2` stops asking for habitual clothing and is told to exclude
it explicitly, the same way it already excludes personality and camera language.

**Migration**: anchors already cached contain clothing. On load, strip tokens matching a
small clothing vocabulary in place — no LLM call, one time, idempotent. Cheaper and less
surprising than blanking anchors and re-deriving.

### D2 — `Character.visual_wardrobe`, fed by a state reinforcement

- New field `visual_wardrobe: str | None`, same shape as `visual_anchor`.
- A managed `Reinforcement` per character: *"What is {name} currently wearing, and what
  visible physical condition are they in?"*, default `interval` 10.
  `auto_update_reinforcments` (`agents/world_state/reinforcements.py:64`) already
  refreshes due reinforcements as the story advances, storing the result in
  `reinforcement.answer`.
- When that answer changes, convert it to keywords once and cache in `visual_wardrobe` —
  reusing `derive-visual-anchor.jinja2` in a third mode.
- Check `insert` mode options before choosing one. We need the stored answer; whether it
  also enters story context is a separate call, and `sequential` would add a
  reinforcement message to history on every refresh.

This is what makes the answer to "when does it update" become *automatically, as the
story advances*, without touching identity.

### D3 — Permanent-change detection, also a reinforcement

A second reinforcement per character, longer interval (~25): *"Has anything permanently
changed about {name}'s appearance — scars, injuries, lost limbs, changed hair?"*

When the answer asserts a change, clear `visual_anchor` so the next image re-derives it.

**This is the riskiest piece.** An LLM asked "has anything changed?" will sometimes say
yes because it wants to be helpful. Guards:

- require an explicit, specific change; treat hedged or empty answers as "no"
- log every invalidation with the answer that caused it, so false positives are
  diagnosable rather than mysterious
- never invalidate more than once per N turns regardless of answers

If false positives prove common, this degrades to manual and the other pieces still
stand.

### D4 — Location-keyed scene anchors

- `Scene.visual_anchors: dict[str, str]`, keyed by normalised location string, with the
  existing `visual_anchor` retained as the fallback for scenes with no location known.
- Key from `world_state.location`, which the world-state snapshot already maintains
  (`agents/world_state/snapshot.py:562-563`).
- At generation: look up the current location's anchor; derive and cache on a miss.
  Revisiting a location reuses its anchor, which is the point of keying rather than
  invalidating.
- Normalise the key hard (lowercase, strip, collapse whitespace) — it is free text from
  an LLM, so "The Control Room" and "control room" must not become two entries.

### D5 — Invalidate on appearance edit

`update_character_attribute`: when the attribute is `appearance`, clear `visual_anchor`.
Five lines, closes P2, costs nothing until the next image.

### D6 — Assembly and conflict resolution

Order: art style, location anchor, identity anchor, wardrobe, `visual_rules`, LLM action
keywords.

**The hard part.** A reinforcement refreshed every 10 turns is less current than what the
scene says right now. If the LLM's keywords say `naked` and wardrobe says
`utility suit`, the LLM must win — and `_build_prompt`'s first-occurrence dedupe cannot
help, because those two are not duplicates, they are a contradiction.

So wardrobe is a **fallback**, suppressed when the LLM's own keywords already speak to
clothing or state. Implemented in `_finalize_prompt`, alongside the existing in-frame
pruning, using a small clothing/nudity/state vocabulary.

That vocabulary is the main maintenance cost of this design, and it will need tuning
against real output exactly as the sanitiser blocklist did.

## Acceptance criteria

1. **AC1 — No wardrobe contradiction.** A scene stating a character is undressed produces
   a prompt containing no cached clothing tokens for that character.
2. **AC2 — Identity survives a wardrobe change.** Changing what a character wears leaves
   their identity anchor byte-identical.
3. **AC3 — Wardrobe follows the story.** After the wardrobe reinforcement refreshes with
   a new answer, the next image's prompt reflects it without any manual step.
4. **AC4 — Appearance edit invalidates.** Editing `base_attributes["appearance"]` causes
   the next image to use a re-derived anchor.
5. **AC5 — Location switch switches anchors.** Moving to a new location produces a prompt
   with that location's tokens and none of the previous location's.
6. **AC6 — Revisiting reuses.** Returning to a known location makes no derivation call.
7. **AC7 — Migration.** An anchor cached with clothing has those tokens stripped on load,
   once, with the rest untouched.
8. **AC8 — False-positive containment.** A permanent-change reinforcement answering "no"
   or hedging never clears an anchor.
9. **AC9 — Playwright evidence**, on the live app as before.

## Out of scope

- Reference-image consistency (still the strongest lever, still deferred).
- Per-character LoRA.
- Rendering fidelity. The previous track's closing note stands: correct prompts are not
  the same as a checkpoint honouring them.

## Hazards

- `scene.environment` is `"scene"` / `"creative"` — a **mode**, not a place. It is not the
  location. Use `world_state.location`.
- Reinforcements carry `require_active`, and character reinforcements are skipped for
  inactive characters (`reinforcements.py:80-86`). A deactivated character's wardrobe
  will therefore go stale — correct behaviour, but it must not be mistaken for a bug.
- `update_reinforcement` pops prior reinforcement messages from history to avoid
  flooding it (`reinforcements.py:163-171`). Adding two reinforcements per character
  multiplies that traffic; watch context growth on a large cast.
- Everything auto-updating costs LLM calls on a cadence. Two reinforcements per character
  on a four-character scene is eight extra periodic queries. Both intervals must be
  configurable, and the whole feature should be switchable off.
