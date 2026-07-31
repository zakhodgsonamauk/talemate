# Plan: visual-reference-closure

Spec: `spec.md`. Design: `docs/fork/visual-reference-consistency-design.md`.

## DAG

```
T1 static guardrails (fork-styles.yaml)
  |
  +--> T2 count-aware negatives in generation.py + unit tests
  |      |
  |      +--> T3 verify single-subject (AC3) and two-character (AC4, AC6)
  |
  +--> T4 photoreal_pony decision (AC7)

T3
  |
  +--> T5 canonical Kaira cover (AC8) -- blocks on user pick
  |
  +--> T6a docs: guardrails + design-doc amendment
         |
         +--> T7a commits: fix, feature, guardrails, docs, launcher   <-- does NOT wait for T5

T5
  |
  +--> T6b docs: canonical-cover outcome
         |
         +--> T7b commit: canonical cover + its doc note
```

Deliberate: the commit chain forks. `T7a` ships everything that does not depend on a
human choosing an image; `T7b` follows whenever the pick happens. The predecessor
track had `T8 ← T7`, which contradicted its own risk note that T7 must not block
commits — the panel review flagged it and this is the correction.

## Tasks

### T1 — Static guardrails, tag-shaped
`templates/world-state/fork-styles.yaml`, `semireal_pony.negative_keywords`. Append
composition-duplication and anatomy-duplication terms per AC1. Tag-shaped, because the
checkpoint is booru-trained and natural-language negatives carry little signal.

Excluded on purpose: `twins` (suppresses a legitimate twin), any count tag (T2 handles
those conditionally), illustration/painting terms (the template's description explains
why they are absent). Leave `positive_keywords` alone — AC5.
Deps: none.

### T2 — Count-aware negatives
Beside `_add_species_negatives` in `generation.py`: when exactly one character is in
frame, add `2girls`, `2boys`, `multiple girls`, `multiple boys` to the negative prompt;
when two or more are, add none of them.

Reuse the in-frame determination already used for reference selection
(`references.py::_characters_in_prompt` / `_subjects_by_anchor`) rather than counting
again. Unit tests for the one-character and two-character branches, and for the case
where nobody is identified (add nothing — the reference selector's own precedent).
Deps: T1.

### T3 — Verify generations
Through the UI, ComfyUI up, one frontend connected only:
- three single-character `SCENE_ILLUSTRATION` generations → one subject each (AC3)
- one two-character moment → two people, and the recorded negative prompt contains no
  count tags (AC4)
- read the negative prompt from asset metadata to confirm the new terms actually reach
  the backend, not just the YAML (AC6)
Then run `tests/test_visual_anchor.py` for AC5.
Deps: T2.

### T4 — `photoreal_pony` decision
Decide whether it gets equivalent terms. It already negatives
`anime, cartoon, illustration, drawing, painting, 3d render`, so its needs differ.
Record the decision and the reason in `conductor/decision-log.md` either way (AC7).
Deps: T1.

### T5 — Canonical Kaira cover  *(user-blocked)*
Generate 3–4 candidates via Visualize → Kaira → Card with instructions for a neutral
standing pose, plain backdrop, wearing the utility suit. Present them; the user picks.
Set the winner as her cover, tag it `reference`, then generate two illustrations to
confirm AC8 — including that no camera framing was inherited, not just no glamour pose.

Do not auto-pick. Choosing a character's canonical appearance is curation.
Deps: T3 (so candidates benefit from the guardrails).

### T6a — Docs: guardrails
- Amend `docs/fork/visual-reference-consistency-design.md` with the guardrail finding
  and the count-aware design.
- `conductor/decision-log.md`: tag-shaped negatives; why `twins` and blanket count tags
  are excluded; the `photoreal_pony` outcome from T4.
Deps: T3, T4.

### T6b — Docs: canonical cover
Amend the design doc's *Known limitations* — the "reference is now the canonical
character, and Kaira's is off-spec" entry becomes resolved, with what the replacement
changed.
Deps: T5.

### T7a — Commits (everything not blocked on the pick)
Confirm the branch state first: the session started on a detached-HEAD snapshot with
`feature/tell-me-a-story` as the branch.

1. `fix(visual): strip emphasis before re-splitting keywords on regenerate` — the
   `strip_emphasis` call and its import together; they are one change.
2. `feat(visual): condition generation on character reference images` — `references.py`,
   agent wiring, both workflow JSONs, `tests/test_visual_reference.py`.
3. `fix(visual): guard against duplicated subjects` — T1 + T2 + their tests.
4. `docs(fork): reference-consistency design and ComfyUI setup` — both docs, plus
   `conductor/tracks/*` and the `images-without-comfyui.md` cross-reference.
5. `chore(launcher): ComfyUI switch with VRAM reserve` — `start-fork.bat`, `.gitignore`.

Excluded deliberately: `scenes/*/assets/library.json` and generated PNGs (disposable).
Decide `.mcp.json` explicitly. Confirm whether `config.yaml` is ignored.
Deps: T6a.

### T7b — Commit the canonical cover
The chosen asset, the character's `cover_image` change, and T6b's doc note.
Deps: T5, T6b.

## Risks

- **Diffusion negatives are guidance, not guarantees.** AC3 may need a second
  iteration with different terms. Budget for one.
- **T2 touches the prompt finaliser**, which the previous track already amended twice.
  Regression surface is the anchor test suite — run it, not just the new tests.
- **AC6 has no existing precedent to lean on.** Nothing currently pins negative-side
  assembly behaviour, so the check has to read asset metadata from a real generation.
- **T5 is user-blocked** and must not hold T7a. The DAG now enforces that structurally.
- **Verification needs the GPU and a free websocket.** ComfyUI plus the Ollama text
  model is tight on 16GB (`--reserve-vram 5` already in the launcher), and a browser tab
  open elsewhere will lock Playwright out of the backend entirely.
- **Commit hygiene is the quiet risk.** Generated-asset churn is easy to sweep in with
  `git add -A`; the memory note that these are disposable exists because it has happened.
