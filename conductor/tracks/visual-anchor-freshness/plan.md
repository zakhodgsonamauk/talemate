# Plan: visual-anchor-freshness

**Spec**: `conductor/tracks/visual-anchor-freshness/spec.md`
**Design doc**: `docs/fork/visual-anchor-freshness-design.md`

TDD on the Python side. Frontend has no runner, so UI is browser-verified.

Commit after each task.

---

## Phase 0 — Split identity from state

### T1 — Identity anchor stops carrying clothing

- [ ] Failing template test: `derive-visual-anchor.jinja2` character mode no longer asks
      for habitual clothing and says so in its exclusions.
- [ ] Edit the template. The exclusion list already covers personality, plot, camera and
      style; clothing joins it.
- [ ] Verify: `pytest tests/prompts/test_visual_templates.py`

**AC2** groundwork.

### T2 — One-time migration of existing anchors

- [ ] Failing tests: an anchor containing `fitted dark blue-grey utility suit` loses that
      token and keeps every other; an anchor with no clothing is untouched; running twice
      changes nothing.
- [ ] `strip_wardrobe_tokens()` in `agents/visual/anchors.py`, applied on character load.
      No LLM call.
- [ ] Verify: `pytest tests/test_visual_anchor.py -k migration`

**AC7**.

---

## Phase 1 — Wardrobe that follows the story

### T3 — `Character.visual_wardrobe`

- [ ] Failing test: field round-trips and defaults to `None`.
- [ ] Add the field beside `visual_anchor` (`character.py:49`); add the manager setter
      mirroring `update_character_visual_anchor`.
- [ ] Verify: `pytest tests/test_visual_anchor.py`

### T4 — Wardrobe derivation mode

- [ ] Failing tests: wardrobe mode renders from a reinforcement answer; emits only
      clothing and visible condition; excludes identity traits.
- [ ] Third mode in `derive-visual-anchor.jinja2` (`anchor_mode == "wardrobe"`), plus
      `AnchorMixin.wardrobe_anchor()`.
- [ ] Verify: `pytest tests/test_visual_anchor.py -k wardrobe`

### T5 — Managed wardrobe reinforcement

- [ ] Decide the `insert` mode first — inspect the options. `sequential` adds a message to
      history on every refresh, which we probably do not want for this.
- [ ] Failing tests: the reinforcement is created once per character, not duplicated on
      reload; a changed answer triggers one wardrobe re-derivation; an unchanged answer
      triggers none.
- [ ] Create/ensure the reinforcement; hook the answer-changed path to `wardrobe_anchor()`.
- [ ] Config: interval, and an off switch for the whole freshness feature.
- [ ] Verify: `pytest tests/test_visual_anchor.py -k reinforcement`

**AC3**.

### T6 — Wardrobe as a suppressible fallback

- [ ] Failing tests, this is the load-bearing one:
      - scene says `naked` → no cached clothing tokens survive (**AC1**)
      - scene says nothing about clothing → wardrobe is injected
      - scene names different clothing → wardrobe suppressed, scene's clothing kept
      - identity anchor is untouched in all three (**AC2**)
- [ ] Extend `_finalize_prompt` with clothing/state conflict suppression, beside the
      existing in-frame pruning. Small vocabulary, logged when it fires.
- [ ] Verify: `pytest tests/test_visual_anchor.py -k wardrobe_conflict`

**AC1**, **AC2**.

---

## Phase 2 — Invalidation

### T7 — Appearance edit clears the anchor

- [ ] Failing test: `update_character_attribute("appearance", ...)` leaves
      `visual_anchor` as `None`; editing any other attribute leaves it alone.
- [ ] Five lines in `world_state/manager.py:334`.
- [ ] Verify: `pytest tests/test_visual_anchor.py -k invalidate`

**AC4**.

### T8 — Permanent-change reinforcement

- [ ] Failing tests: a specific assertion of change clears the anchor; `"no"`, `""` and a
      hedged answer do not (**AC8**); two changes inside the rate-limit window cause one
      invalidation; every invalidation is logged with its cause.
- [ ] Second reinforcement, longer interval, with the guards from the design doc.
- [ ] Verify: `pytest tests/test_visual_anchor.py -k permanent`

**AC8**. If this proves noisy in practice, disable it and keep T7 — the rest of the track
does not depend on it.

---

## Phase 3 — Location-keyed setting

### T9 — `Scene.visual_anchors` keyed by location

- [ ] Failing tests: key normalisation collapses `"The Control Room"` and
      `"control room"`; a miss derives and caches; a hit makes no derivation call
      (**AC6**); no location falls back to `visual_anchor`.
- [ ] Add the dict, its five plumbing sites (mirroring `visual_anchor` in T2 of the
      previous track), and lookup from `world_state.location`.
- [ ] Verify: `pytest tests/test_visual_anchor.py -k location`

**AC5**, **AC6**.

### T10 — Assembly uses the location anchor

- [ ] Failing test: prompt carries the current location's tokens and none from the
      previous location.
- [ ] Point `_insert_anchors` at the location lookup.
- [ ] Verify: `pytest tests/test_visual_anchor.py`

**AC5**.

---

## Phase 4 — UI and verification

### T11 — UI

- [ ] Wardrobe field beside Appearance Keywords, marked as auto-maintained and showing
      when it last refreshed.
- [ ] Scene settings: show the per-location anchors, editable, with the active one marked.
- [ ] Interval and off-switch controls on the visual agent.
- [ ] Verify: browser.

### T12 — Playwright E2E

- [ ] AC1-AC8 against the live app, review queue as before.
- [ ] Drive a real wardrobe change through the story rather than by editing the field, so
      **AC3** is proven end to end and not just unit-tested.
- [ ] Move the party to a second location and confirm **AC5**, then return and confirm
      **AC6** makes no derivation call.
- [ ] Screenshot evidence (**AC9**).

### T13 — Docs

- [ ] `FORK.md` rows; design doc status and any amendments; `conductor/decision-log.md`.
- [ ] Record what the E2E disproved. Last track's amendments section is the format — and
      the expectation, not a formality.

---

## DAG

```
T1 ─> T2 ─┐
T3 ─> T4 ─┴─> T5 ─> T6 ─┐
T7 ──────────────────────┤
T8 ──────────────────────┤
T9 ─> T10 ───────────────┴─> T11 ─> T12 ─> T13
```

| Group | Tasks | Note |
|---|---|---|
| G1 | T1, T3, T9 | independent: template, field, location dict |
| G2 | T2, T4, T10 | each needs its G1 sibling |
| G3 | T5 → T6 | sequential; T6 is the load-bearing one |
| G4 | T7, T8 | independent of the wardrobe chain |
| G5 | T11 → T12 → T13 | |

## Complexity

**M-L** — 13 tasks. Smaller than the previous track because the anchor machinery,
`_finalize_prompt` and the derivation template all already exist. The cost is concentrated
in two places: T6's conflict suppression and T8's false-positive containment.

## Hazards

Carried from the design doc:

- `scene.environment` is a mode (`"scene"`/`"creative"`), not a location. Use
  `world_state.location`.
- Character reinforcements are skipped when the character is inactive
  (`reinforcements.py:80-86`). A deactivated character's wardrobe going stale is correct,
  not a bug.
- `update_reinforcement` pops prior messages to avoid flooding history
  (`reinforcements.py:163-171`). Two reinforcements per character multiplies that.
- Two reinforcements per character on a four-character scene is eight extra periodic LLM
  queries. Intervals configurable; feature switchable off.
- First-occurrence dedupe cannot resolve `naked` against `utility suit`. Only explicit
  suppression can.
