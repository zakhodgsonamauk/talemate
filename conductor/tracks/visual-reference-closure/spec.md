# Track: visual-reference-closure

**Type**: feature (polish + hygiene)
**Created**: 2026-07-30
**Follows**: `visual-reference-consistency`
**Design doc**: `docs/fork/visual-reference-consistency-design.md` (read it — this spec does not repeat it)

Closes out the reference-conditioning work: the two things that still make its output
look unfinished, and the commit it has been waiting for.

Supersedes the abandoned `scene-scroll-and-image-quality` track. See
*Appendix: parked scroll investigation* for why P1 there was dropped.

## Problems

### P1 — Illustration prompts have no anti-duplication guardrails

The app sends only the style template's eight negatives:
`text, watermark, low quality, blurry, deformed, extra limbs, bad hands, bad anatomy`.
Nothing discourages a duplicated subject. Live generations produced two, and in one
case four, copies of the subject, in compositions unrelated to the story beat. The
reference-conditioning probes that looked correct during the previous track used a
hand-written negative list the app never sends.

Two facts constrain the fix:

- `CyberRealisticPony_V9` is booru-tag trained, so natural-language negatives are
  weak. Tag-shaped terms carry far more signal.
- Character count is legitimately variable. `MAX_CHARACTERS_IN_FRAME` is 3
  (`anchors.py`), so a blanket `multiple girls` negative would fight a legitimate
  three-character shot, and `2girls` would fight a two-hander.

### P2 — Kaira's cover drives pose, not just identity

Reference conditioning reproduces the reference's framing along with its identity
(established last track, measured). Her cover is a glamour portrait, so illustrations
inherit that stance and ignore prompt action keywords. The mechanism is working
correctly on an unsuitable input.

### P3 — A day of visual work is uncommitted

Seven modified files, seven new, plus two new track directories. Includes disposable
generated-asset churn that must **not** be committed.

## Goal

Scene illustrations show one subject unless the scene genuinely has more. Kaira's
reference is a neutral full-body image, so illustrations inherit her identity without
her cover's pose. The work is committed in reviewable pieces, with nothing disposable
swept in.

## Acceptance criteria

1. **AC1 — Static guardrails, tag-shaped.** `fork_styles__semireal_pony`
   `negative_keywords` gains duplication terms that are safe at any character count:
   composition duplication (`split image`, `multiple views`, `multi-panel`, `mirror
   image`) and anatomy duplication (`cloned face`, `extra torso`, `extra heads`,
   `extra arms`, `extra legs`). Natural-language-only terms are not relied on, and
   `twins` is excluded — it would suppress a legitimate twin.
2. **AC2 — Count-aware negatives, injected dynamically.** When exactly one character
   is in frame, `2girls`/`2boys`/`multiple girls`/`multiple boys` are added to the
   negative prompt; when two or more are in frame, they are not. Implemented beside
   the existing `_add_species_negatives` hook in `generation.py`, reusing the
   in-frame evidence the reference selector already computes. Unit-tested both ways.
3. **AC3 — Single-subject shots are single-subject.** Three consecutive
   `SCENE_ILLUSTRATION` generations of a one-character moment each show one subject.
4. **AC4 — Multi-character shots still work.** A moment naming two characters renders
   two people, and the recorded negative prompt contains no `2girls`/`multiple girls`.
5. **AC5 — Positives untouched.** `semireal_pony.positive_keywords` is unchanged, and
   the existing test pinning that the Semi-Real (Pony) tag set survives the sanitiser
   still passes.
6. **AC6 — New negatives survive assembly.** The added terms appear in the negative
   prompt actually sent to ComfyUI, verified from asset metadata — not merely present
   in the YAML. (The sanitiser operates on the assembled prompt; nothing currently
   pins negative-side behaviour.)
7. **AC7 — `photoreal_pony` decided, not forgotten.** Either it receives equivalent
   terms or it explicitly does not, recorded in the decision log with a reason.
8. **AC8 — Canonical reference.** Kaira's `cover_image` is a full-body, clothed,
   neutral-pose image on a plain backdrop, tagged `reference`. Two illustrations
   generated afterwards show her violet skin and markings, in prompt-directed poses,
   with no inherited glamour stance and no inherited camera framing.
9. **AC9 — Committed cleanly.** Working tree clean apart from deliberately-ignored
   files. `scenes/*/assets/library.json` churn and generated images are **not**
   committed. Separate commits for: the keyword-emphasis behaviour fix, the reference
   feature (code + workflows + tests), the style guardrails, the docs, and the
   launcher change.

## Non-goals

- Anything about page scrolling or Vuetify layout — see the appendix.
- Making the inpaint workflow the default; it stays available and documented.
- Per-character LoRA; multi-character regional references.
- Changing `image_max_tokens` again (77 → 250 already happened, during the previous
  track's verification).

## Technical notes

**Style template**: `templates/world-state/fork-styles.yaml`, `semireal_pony`. Its
description explains why illustration/painting terms are deliberately absent from its
negatives — do not add them.

**Dynamic injection**: `generation.py` already has `_add_species_negatives`, called
from `_finalize_prompt`, and `references.py` already determines who is in frame
(`_characters_in_prompt`, `_subjects_by_anchor`). AC2 should reuse that, not
reimplement counting.

**Commit hygiene**, established by inspection:

- `strip_emphasis` appears nowhere in `HEAD`. The uncommitted work added the call and
  the import is part of the same change — this is one behaviour fix (strip emphasis
  before re-splitting so regenerate does not compound weights), **not** a separable
  import fix. An earlier draft of this track claimed a committed `NameError`; that was
  wrong.
- Untracked and needing decisions: `templates/comfyui-workflows/sdxl-ipadapter-*.json`
  (belong with the feature), `conductor/tracks/*` (belong with the track), `.mcp.json`
  (decide: it names a local MCP server, so probably yes but confirm).
- `config.yaml` — confirm its ignore status rather than assuming.
- `scenes/infinity-quest-dynamic-story-v2/assets/library.json` and the PNGs are
  disposable test output. Leave them out.

## Verification

AC1, AC2, AC5, AC7 by unit test and file inspection. AC3, AC4, AC6, AC8 need real
generations, so ComfyUI must be up (`start-fork.bat` with `USE_COMFYUI=1`) and only
one frontend may be connected — the backend accepts a single websocket, so a browser
tab open elsewhere will lock Playwright out.

## Appendix: parked scroll investigation

The predecessor track opened with a claim that the story pane never scrolls, so the
page overflows and the bottom toolbar sits below the fold. Measurements were real
(document 2189px in a 1500px viewport; `.scene-container` `scrollHeight ==
clientHeight`), but **the conclusion was wrong and the item is dropped**:

- Page-level scrolling is this app's deliberate design.
  `TalemateApp.vue:483`, `:632`, `:635`, `:1282` scroll the window / the message input
  into view after messages, so normal use is parked at the bottom.
- Every measurement was taken inside Playwright, whose window reported
  `devicePixelRatio: 0.8` (CSS viewport 2400×1350, not the 1920×1080 requested), and
  whose viewport had been resized repeatedly mid-session.
- The immediate cause of the unreachable toolbar was that Playwright had been refused
  the websocket — *"Another Talemate frontend is already connected"* — so no scene
  loaded and no auto-scroll fired. Closing the other tab resolved it.

Two corrections worth keeping if anyone revisits this: Vuetify's `.v-main` is
`flex: 1 1 auto` (not `1 0 auto`), and it carries an inline `height: 100%` at
`TalemateApp.vue:110`; the unclamped hops would be `v-app`'s `min-height: 100vh` and
the height-less `v-row`/`v-col`. Three CSS probe attempts failed, one by matching
Vuetify's grid globally and ballooning the document to 5408px. Independent story-pane
scrolling remains a *possible* design improvement, but it would fight the existing
auto-scroll logic and has no user-reported problem behind it.
