# Visual subject fidelity — implementation plan

**Spec**: `conductor/tracks/visual-subject-fidelity/spec.md`

Ordered so each step is independently verifiable, and so the cause of any improvement is
attributable. That is why IPAdapter is untouched until the end and why the budget reduction
comes last.

Regression surface for every task: `tests/test_visual_anchor.py` (130 tests) and
`tests/test_visual_reference.py` (19). Run both, not just new tests.

---

## Phase 1 — Sex conditioning (R1) — **DONE**

### [x] T1 — Booru sex tags at the front of the positive prompt

`SEX_TAGS` in `generation.py`: `male → 1boy, male focus`; `female → 1girl`. Added by
`_add_sex_tags`, inserted at the head of the keyword list and placed **before**
`_trim_to_budget` so the tags are budgeted rather than squeezed out.

Front position is deliberate: attention thins across the prompt, and a sex tag behind
`solo, looking at viewer` does not hold.

`normalise_sex` maps free-form `Character.gender` (`character.py:115`) onto male/female, and
returns None for blank, `non-binary`, or ambiguous values — adding nothing rather than
guessing, because a wrong tag actively fights the prompt.

Word-boundary matching is load-bearing: `"male" in "female"` and `"man" in "woman"` are both
True, so substring tests invert the sex. Explicitly tested.

**Acceptance**: AC3. Done.

### [x] T2 — Opposite-sex and conditional nudity negatives

`_add_sex_negatives`, beside `_add_species_negatives` — which returns early for humans, so a
human subject previously had no correction at all.

Opposite-sex negatives always. `NUDITY_NEGATIVES` only when a clothing word is present in the
keywords, so an undressed scene keeps its own intent; the scene text is the authority on what
they are wearing.

**Acceptance**: AC3. Done. 38 tests added
(`tests/test_visual_sex_conditioning.py` plus behavioural tests in `test_visual_anchor.py`),
including one asserting no tag appears on both sides of the same generation.

### [ ] T3 — Verify the wrong-person fix by generation

Human-only. ComfyUI up, one frontend connected.

1. Regenerate the Zak image that failed. Expect male, dressed.
2. One female-subject generation, to confirm nothing inverted.
3. One deliberately undressed generation, to confirm nudity negatives stayed out.
4. Read the negative prompt from asset metadata to confirm the terms reached the backend
   rather than only the config.

**Acceptance**: AC1, AC2. **Deps**: T1, T2. **Verification**: play.

---

## Phase 2 — Stop telling action shots to face the camera (R2)

### [ ] T4 — Establish why a scene moment became a CHARACTER_CARD

Investigation, and it gates the fix. The observed generation carried `CHARACTER_CARD`,
`PORTRAIT` and `IMAGE_EDIT`, for what was conceptually "what is happening right now".

`looking at viewer` and `solo` are hardcoded in `templates/world-state/visual-styles.yaml`
at `:41` (character_card) and `:57` (character_portrait). If an action moment is being
routed to a portrait vis type, the tag is a symptom and vis-type selection is the defect.

Determine which of these is true, and record it:
- the visualise entry point chose `CHARACTER_CARD` for a scene moment, or
- the vis type was correct and the style is simply wrong for a character *doing* something.

**Acceptance**: a recorded finding in `conductor/decision-log.md` naming the fix location.

### [ ] T5 — Remove the camera-gaze tag from action prompts

Implementation follows T4's finding. Whichever location, the behaviour required is the same:
a prompt whose action implies attention elsewhere must not also carry `looking at viewer`.

Deliberate portraits must keep it — that is what AC5's second half pins.

Failing tests first: an action prompt loses the tag, a portrait keeps it.

**Acceptance**: AC5. **Deps**: T4.

---

## Phase 3 — Recover the budget (R3)

### [ ] T6 — Drop vocabulary that cannot be drawn

A stop-list applied before `_trim_to_budget`, in the shape of the existing
`_drop_secondary_traits`.

Seed it from the ~40 tokens observed surviving into a live prompt: `concentrated,
experienced, determined, problem solving, technical, precision, practiced, analysis,
skilled, professional, reserved, quiet, stressed, situation, critical, time pressure,
crisis, troubleshoot, repair, monitoring, tracking, practical, intense`.

Two constraints:
- Visual adjectives must survive. `wrinkled`, `grease stain`, `worn`, `dim`, `disheveled`
  all draw and must not be caught.
- Must not contradict `_BODY_AND_POSE_WORDS` (`generation.py:55`), which exists to protect
  person-and-action keywords from other filters.

Failing tests first, asserting both the drops and the survivals, measured as a token count
recovered on the observed prompt.

**Acceptance**: AC6.

---

## Phase 4 — Make the action hold (R4)

### [ ] T7 — Keep the action as one weighted phrase

"Leaning over the console" currently arrives as `standing, lean, hands hovering, interface`
— the subject-object relationship destroyed by comma splitting, and unweighted while the
appearance anchor carries `:1.3`.

Emit the action as a coherent emphasised group, reusing the existing `weight_group` /
`_render_with_emphasis` machinery rather than a second emphasis implementation. Mind
`strip_emphasis` (added by an earlier fix) so regenerate does not compound the weight.

**Acceptance**: AC7. **Deps**: T6, so the budget exists to spend on it.

---

## Phase 5 — Fit the prompt to the attention window (R5)

### [ ] T8 — Reduce the effective token budget, measured

`image_max_tokens` is `250`; `DEFAULT_MAX_PROMPT_TOKENS` is `77`. The observed prompt was
~196 tokens with the action near token 95.

Measure what T6 recovered and what T7 costs, then set the budget from that — toward one CLIP
chunk, but the number comes from the measurement, not from the constant.

**Acceptance**: AC8. **Deps**: T6, T7.

### [ ] T9 — Verify the wrong-thing fix by generation

Human-only. The console-leaning case, plus one other distinctive action.

**Acceptance**: AC4. **Deps**: T5, T7, T8. **Verification**: play.

---

## Phase 6 — Close out

### [ ] T10 — IPAdapter decision, recorded

Only now, with conditioning trusted. `weight 0.8`, `weight_type: ease out`, `end_at: 0.8`
in `sdxl-ipadapter-character.json`. If identity still drifts after T3, consider
`weight_type: linear` and `end_at: 1.0` — one change at a time.

**Acceptance**: a recorded decision, whether or not anything changes. **Deps**: T3, T9.

### [ ] T11 — Docs

Amend `docs/fork/visual-reference-consistency-design.md` with the tag-vocabulary finding:
this checkpoint family is booru-trained, so natural language in *either* the positive or the
negative carries little signal. The closure track recorded that for negatives; it is equally
true of the positive, which is what caused the wrong-sex render.

**Acceptance**: docs match the implementation. **Deps**: T9, T10.

---

## DAG

```
T1 ──┐
T2 ──┴─> T3 ─────────────┐
T4 ──> T5 ──┐            ├─> T10 ──> T11
T6 ──> T7 ──┴─> T8 ──> T9 ┘
```

Parallel groups:

- **G1**: T3 (human), T4, T6 — independent
- **G2**: T5, T7
- **G3**: T8
- **G4**: T9 (human)
- **G5**: T10, T11

## Complexity

**M** overall. T1/T2 done (S). T4 S, T5 S, T6 M, T7 M, T8 S, docs S. Human verification
T3 and T9.

## Risks

- **The finaliser is heavily amended already.** Three previous tracks touched it. The
  130-test anchor suite is the guard; treat a failure there as a real regression rather than
  a stale expectation.
- **Over-culling in T6.** A stop-list that catches visual adjectives makes images worse, and
  will not be obvious from tests alone. Keep it explicit rather than pattern-based.
- **Attribution.** If IPAdapter is touched before T9, no result can be attributed. R6 exists
  for this reason.
- **Diffusion guidance is not a guarantee.** AC1 and AC4 may need a second iteration with
  different tags. Budget for one.
- **GPU contention.** ComfyUI plus the local text model is tight on 16GB; the launcher
  reserves 5GB. Verification needs both up.
