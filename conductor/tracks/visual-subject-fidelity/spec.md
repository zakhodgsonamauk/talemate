# Visual subject fidelity — specification

**Type**: feature (prompt assembly)
**Created**: 2026-07-31

The image should show **the right person doing the right thing**. Two failures observed
live, with the same underlying cause.

## Problem

### The wrong person

Zak — a human male, with a bearded male reference image attached — was generated as a
half-naked female. Evidence from the review modal and `templates/comfyui-workflows/sdxl-ipadapter-character.json`:

- Checkpoint is `CyberRealisticPony_V9.0_FP16` — Pony-derived, **booru-tag trained**, and
  its default subject is a young nude female.
- The positive prompt said `(human male, ..., strong jawline, ...:1.3)`. "human male" is
  natural language; the tags the model actually learned are `1boy` / `male focus`. So the
  strongest real tags present were `score_9, score_8_up, score_7_up, solo,
  looking at viewer` — every one pulling toward the default.
- The negative prompt was `text, watermark, low quality, blurry, deformed, extra limbs,
  bad hands, bad anatomy`. Nothing opposed the default: no `1girl`, no `female`, no
  `nude`. `_add_species_negatives` returns early when species is human, so a **human**
  subject had no correction at all.
- `grep -rniE "1boy|1girl|male focus|gender|sex"` over `agents/visual/` and
  `fork-styles.yaml` returned nothing — there was no sex handling in the pipeline.
- IPAdapter was never going to rescue it: `weight 0.8`, `weight_type: ease out`,
  `end_at: 0.8`. It transfers facial and style features while sex and composition are set
  by text conditioning in the early steps, and its influence is deliberately decayed.

### The wrong thing

Poses come out generic — a character described as leaning over a console is rendered
standing, facing camera. Manual prompt editing fixes it, which is what this track exists
to remove. Three contributing causes, all measured:

1. **`looking at viewer` is structurally opposed to the action.** It is hardcoded in
   `templates/world-state/visual-styles.yaml:41` and `:57`, in the Character Card and
   Character Portrait styles, beside `solo`. The observed generation was tagged
   `CHARACTER_CARD`. That tag means "gaze at camera", the opposite of "leaning over a
   console", and it sits at roughly token 20 — prime attention.
2. **The prompt is far past useful attention.** `image_max_tokens` is `250` in config while
   `DEFAULT_MAX_PROMPT_TOKENS` is `77` (one CLIP chunk). The observed prompt measured
   **~196 tokens**. `style.py:39` already records the finding that at 155 tokens "roughly
   half of it was past the point where the encoder meaningfully attends". The action words
   (`hands hovering`, `interface`) landed near token 95.
3. **The action is atomised and unweighted.** "Leaning over the console" arrived as
   `standing, lean, hands hovering, interface` — the relationship between subject and
   object destroyed by keyword splitting. The appearance anchor gets `:1.3`; the action
   gets nothing. Roughly 40 tokens are unrenderable abstractions — `determined,
   experienced, professional, precision, skilled, analysis, problem solving, troubleshoot,
   situation, crisis, time pressure` — which draw nothing and crowd out what does.

## Goal

A character described as doing something specific is rendered as that sex, dressed as the
scene says, doing that thing — without hand-editing the prompt.

## Requirements

### R1 — Sex conditioning (DONE, T1/T2)

Booru sex tags at the front of the positive prompt, opposite-sex negatives, and nudity
negatives only when the prompt says the subject is dressed. Free-form or ambiguous gender
adds nothing rather than guessing.

### R2 — Action shots must not be told to face the camera

`looking at viewer` must not survive into a prompt whose action implies attention
elsewhere.

Open question to resolve first: the observed generation was `CHARACTER_CARD` for what was
conceptually a scene moment. Establish whether the fix belongs in vis-type selection (an
action moment should be `SCENE_ILLUSTRATION`), in conditional removal of the tag, or both.

### R3 — Recover the token budget from words that cannot be drawn

Abstract trait and process vocabulary must be dropped before the budget trim. Visual
adjectives must survive — this is a stop-list of the unrenderable, not a general cull.

### R4 — The action must be a weighted phrase

What the subject is doing must reach the model as a coherent, emphasised phrase rather than
scattered single keywords, mirroring what the appearance anchor already does.

### R5 — Fit the prompt to where attention is

Once R3 and R4 land, reduce the effective prompt budget toward one CLIP chunk. The number
is a measurement, not a guess: it depends on how much R3 recovers.

### R6 — Do not touch IPAdapter yet

`weight`, `weight_type` and `end_at` stay as they are until R1-R5 are verified. Changing
conditioning and reference strength together makes it impossible to attribute the result.

## Acceptance criteria

- **AC1** — A male character with a male reference generates as male. Verified by
  generation, not by test. *(R1)*
- **AC2** — A dressed subject is not rendered nude; an undressed scene is still honoured.
  *(R1)*
- **AC3** — Ambiguous or absent gender adds no sex tags or negatives. *(R1, unit-tested)*
- **AC4** — A character described leaning over a console is rendered leaning over a
  console, gaze on their work. Verified by generation. *(R2, R4)*
- **AC5** — `looking at viewer` is absent from action-shot prompts and still present for
  deliberate portraits. *(R2)*
- **AC6** — Abstract vocabulary is absent from the finalised prompt while visual detail
  survives. Unit-tested against the ~40 observed tokens. *(R3)*
- **AC7** — The action appears as one weighted phrase in the finalised prompt. *(R4)*
- **AC8** — Finalised prompts for the observed cases fit the reduced budget. *(R5)*
- **AC9** — `tests/test_visual_anchor.py` and `tests/test_visual_reference.py` still pass
  at every step — this track edits the prompt finaliser, which those suites pin.

## Out of scope

- IPAdapter tuning (R6). Its own follow-up once conditioning is trusted.
- The duplication guardrails owed by `visual-reference-closure` T1/T2, still unimplemented.
  Same file, different defect; do not conflate them.
- Changing the checkpoint. The model's prior is the force being worked with, not a bug.
- Re-deriving the LLM prompt-writing template. This track shapes what the finaliser does
  with the keywords it is given.

## Technical notes

Everything lands in `src/talemate/agents/visual/generation.py::_finalize_prompt`, which is
an ordered chain: `sanitise` → `drop_absent_anchors` → `suppress_stale_wardrobe` →
`drop_duplicate_setting` → `drop_secondary_traits` → `add_sex_tags` → `trim_to_budget` →
dedupe → negatives → `render_with_emphasis`. Position in that chain is load-bearing:
dedupe keeps the first occurrence, so ordering decides which wording wins, and anything
added after `trim_to_budget` escapes budget accounting.

The prompt finaliser has been amended repeatedly by previous tracks. The regression surface
is `tests/test_visual_anchor.py` (130 tests) — run it, not just new tests.

`_BODY_AND_POSE_WORDS` (`generation.py:55`) already exists to protect person-and-action
keywords from setting-duplication filtering. R3's stop-list must not contradict it.
