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

## Phase 1b — What the first live verification actually showed (T3, partial)

The sex fix worked: the subject came back male and bearded, matching the reference. Three
separate defects were exposed underneath it, added here rather than folded into the earlier
tasks because each has its own cause.

### [x] T12 — Nudity negatives must cover dressed-and-exposed

`nude, naked, topless` described none of what happened: the subject was wearing jeans and a
tank top, with the trousers open and genitals exposed. He was not nude — he was dressed and
exposed, which those three tags do not name.

`NUDITY_NEGATIVES` extended with the anatomy and state-of-undress tags: `bottomless, penis,
exposed genitals, pubic hair, nipples, undressing, unzipped, open pants, uncensored, nsfw`.

Also refined the dressed test. A clothing word alone is not proof: "unbuttoning his shirt"
names a garment while describing its removal, so `_UNDRESS_INTENT_WORDS` now suppresses the
nudity negatives when the prompt describes undress, in either direction. The scene text is
the authority.

The two negative sets deliberately overlap on `nipples` — unconditional for a male subject,
conditional for a dressed female one — so collection filters against what has already been
gathered as well as what is already in the prompt, or the tag would be emitted twice.

**Acceptance**: AC2, retested by generation. Cheapest of the three and reversible, so it goes
first: if it alone fixes the explicitness, T14 may not be needed.

### [ ] T13 — The reference image's setting is leaking into the scene

The prompt said `control room, sci-fi, holographic displays, metallic surfaces, blue-white
lighting`. The generated image was an outdoor garden — plants, a bird, a water bottle,
daylight. The reference photograph is a bearded man outdoors, and the IMAGE_EDIT/IPAdapter
path imported his background along with his face.

Arguably worse than the explicitness for scene illustration: it puts the engineer in a
garden during a reactor emergency. Identity transfer is wanted; setting transfer is not.

Investigate in this order, one change at a time so the cause stays attributable:
- IPAdapter `weight_type` and `end_at` in `sdxl-ipadapter-character.json` — `ease out` and
  `0.8` shape how long the reference dominates.
- Whether the edit workflow is running a denoise low enough to preserve the reference
  composition, which would make this a workflow defect rather than a weight one.
- Whether an `IPAdapter` attention mask or a face-only crop is the right shape of fix.

Note this partially overlaps R6/T10, which deferred IPAdapter tuning until conditioning was
trusted. Conditioning is now trusted for sex, so that deferral no longer blocks this.

**Acceptance**: a scene illustration keeps the scene's setting while keeping the character's
identity. Verified by generation. **Deps**: T12.

### [ ] T14 — Switchable model profiles, selected by content

Asked for directly: use a non-NSFW checkpoint for non-NSFW images.

The plumbing already exists. `comfyui.py:1000-1012` resolves a handler per generation type
carrying both a workflow and a model, and `set_main_model` (`:190`) overwrites `ckpt_name` in
the workflow at request time — so the checkpoint is already decoupled from the workflow file
and chosen per request. The hook for content-based selection goes immediately before that.

The catch is that a checkpoint is not swappable alone. `start-fork.local.bat` already records
why: the Pony checkpoints need `score_9, score_8_up, score_7_up` with steps 30, cfg 5-7,
DPM++ 2M Karras, while `sd_xl_turbo_1.0_fp16` needs no score tags, steps ~6, cfg ~1, Euler a.
Swapping the name alone produces mush.

So the unit is a **profile**: checkpoint + sampler settings + style tags, selected together.
ComfyUI currently reports three checkpoints installed —
`CyberRealisticPony_V9.0_FP16` and `CyberRealistic_PonySemi_V5` (both uncensored) and
`sd_xl_turbo_1.0_fp16` (censored, per the launcher's own note).

Open question for the design: what selects the profile. Candidates are the presence of
undress intent in the prompt (the signal T12 already computes), the scene's content
classification, or an explicit per-scene setting. Decide before implementing.

**Acceptance**: an SFW-classified generation uses the SFW profile with sampler settings that
suit it, and an explicit scene still uses the uncensored profile. **Deps**: T12, and T13 if
its finding changes the workflow.

### [ ] T15 — Another character's traits are leaking into the subject's prompt

**Root cause of the second failed verification, and the highest-value defect on this track.**

The subject was Zak, a human male. His prompt contained `purple skin`, `bare chest`,
`Altrusian`, `geometric patterns on arms`, `combat trousers`, `pulse pistol` — all Kaira's.
Two consequences, not one:

1. The image is wrong on its own terms: contradictory adjectives on the subject.
2. It *disarmed a guardrail*. `bare chest` is an undress phrase, so the dressed test read
   the scene as wanting undress and suppressed every nudity negative. The explicit output
   followed from another character's trait.

`_drop_secondary_traits` exists for exactly this and did not catch it. The reason is
visible in `_primary_and_secondary`: traits are matched from each character's **anchor
tokens**, and the LLM paraphrases — it writes `purple skin` where Kaira's anchor says
`deep violet skin`. Word-level matching (`words_of`, len > 3) catches `violet` but not
`purple`, and nothing at all catches `Altrusian` unless it happens to be in her anchor.

Candidate approaches, to be chosen after reading the existing matcher:
- Widen the per-character vocabulary beyond the anchor to the full attribute set, so
  species and gear terms are matchable.
- Drop traits that contradict the subject's own attributes — a human subject cannot have
  `purple skin`, whatever wrote it.
- Both, since they fail differently.

**Acceptance**: a two-character scene produces a subject prompt containing none of the
other character's identity, species or gear vocabulary, including paraphrases. Unit-tested
against the observed prompt. **Deps**: none — independent of the pose work.

### [ ] T16 — Decide "is the subject dressed" from the subject, not from keyword lists

The keyword approach has now failed twice in one session, in both directions: `bare metal`
read as undress, and a leaked `bare chest` disarming the negatives for a fully dressed man.
Counting garments against undress phrases is a stopgap, not an answer — it reasons over the
whole prompt, which describes everyone present, when the question is about one person.

The right signal already exists and is per-character. `character.visual_wardrobe`
(`anchors.py:41`, `WARDROBE_QUESTION`, refreshed every `DEFAULT_WARDROBE_INTERVAL` = 10
turns) is an LLM-written statement of what *that* character is wearing, and
`_suppress_stale_wardrobe` already reasons about it against what the scene says now.

Design:
- Ask the question of the **subject**: is this character, right now, dressed? Read
  `visual_wardrobe` first, then what the scene says about them this moment — the precedence
  `_suppress_stale_wardrobe` already establishes, where the scene wins because undressing is
  a single beat the reinforcement will not notice for several more.
- Because it is scoped to one character, another character's `bare chest` can never reach
  it. That alone fixes the observed failure.
- Escalate to an explicit LLM judgement only when those sources are absent or conflict. A
  per-image call to the cloud model is affordable but should not be the default path for a
  question already answered on an interval.
- Keep the keyword counting as the last-resort fallback for scenes with no reinforcement
  data at all, and say so in the code rather than leaving it as the primary mechanism.

**Acceptance**: the observed contaminated prompt yields nudity negatives, a deliberate
undress beat does not, and neither outcome depends on vocabulary lists. **Deps**: T12. Best
done alongside T15, since both concern attributing prompt content to the right character.

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
