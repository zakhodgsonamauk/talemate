# Visual consistency — design

**Status**: design agreed, not implemented
**Date**: 2026-07-30
**Applies to**: `SCENE_ILLUSTRATION` first; `CHARACTER_CARD` / `SCENE_CARD` /
`SCENE_BACKGROUND` inherit the same machinery.

## Problem

Generated illustrations show a different person every time. The character is not
recognisable shot to shot, the setting drifts, and a large share of the prompt is
spent on words that carry no visual meaning.

### Evidence

Review queue entry, request `77ae2130-35ec-48cd-9eb7-38f890725be6`, scene
`infinity-quest-dynamic-story-v2`, backend `automatic1111`, `1216x832`:

The image shows one uniformed man alone in a derelict room with **mountains** through
the windows. It should be a starship control room with two crew members.

The `Instructions` field held the right information:

> The control room's overhead lights cast harsh shadows across the metallic
> surfaces... Kaira remains where she stood... Her violet skin catches the blue-white
> glow of the screens... The geometric patterns on her forearms seem to shimmer...

The prompt actually sent to A1111 did not:

```
score_9, score_8_up, score_7_up, semi-realistic, detailed painterly rendering,
realistic anatomy, cinematic lighting, sharp focus, horizontal, landscape, cinematic,
dynamic, action, interaction, sterile control room, flickering displays, exhausted
captain, alert alien officer, corruption, diagnostic, waiting, watching, tense
moment, geometric patterns, violet skin, dark circles, focused
```

## Root causes

Four, stacked. All four have to go.

### RC1 — Nothing anchors character identity

Canonical appearance exists and is good.
`character_data.Kaira.base_attributes.appearance`:

> Just over six feet, lean and angular. Deep violet skin with faint geometric
> patterns along her forearms and jaw... Large dark eyes, no visible iris. Hair is a
> deep indigo, worn pulled back and secured. Four-fingered hands. Wears a fitted
> utility suit in dark blue-grey...

`generate-image-SCENE_ILLUSTRATION.jinja2:6` never injects it. Instead it fires a
`batch_query_scene` RAG query per character asking the LLM to re-describe appearance
**on every generation**. A non-deterministic source cannot produce a consistent
subject. `Kaira` as a bare token means nothing to an SDXL/Pony checkpoint.

### RC2 — Keyword compression drops the world

`prompt_type: KEYWORDS` (config.yaml, `automatic1111_image_create`). The LLM's prose
is discarded and its keyword list is what ships. In the sample, **zero** setting
tokens survived — no `spaceship`, `starship bridge`, `sci-fi`, `space`. SD saw
`control room` plus windows and painted mountains.

What did survive: `corruption, diagnostic, waiting, watching, tense moment, focused`.
Plot and state words. They render as nothing. These are the "spike phrases".

### RC3 — Template meta-instructions leak into the image prompt

`generate-image-SCENE_ILLUSTRATION.jinja2:31-52` instructs the prompt-writing LLM to
"emphasize the horizontal/landscape format and the dynamic, moment-capturing nature".
The LLM obeys by putting `horizontal, landscape, cinematic, dynamic, action,
interaction` in the prompt. Format is already fixed by the resolution (1216x832).
Roughly a third of the usable tokens, wasted, every time.

### RC4 — `visual_rules` is silently dropped

Elmer's rule — "always has the head / face rendered completely in shadows" — is
handed to the LLM at template line 23 and does not reach the final prompt. The image
shows a lit face. A rule labelled HARD is advisory in practice.

## Design

### D1 — Visual anchors (new persisted data)

Two new fields, both `str | None`, holding a comma-delimited keyword list:

| Field | Location | Precedent |
|---|---|---|
| `Character.visual_anchor` | `src/talemate/character.py`, beside `visual_rules:48` | `visual_rules` |
| `Scene.visual_anchor` | beside `Scene.visual_style_template` | `visual_style_template` |

Resolution order per character, evaluated at prompt-build time:

1. `character.visual_anchor` if set — used verbatim, no LLM call
2. else derive once from `base_attributes.appearance` via a single LLM call, **write
   the result back** to `visual_anchor`, persist with the scene
3. else no anchor for that character (log it)

Derivation is a new template `visual/derive-visual-anchor.jinja2`, returning
`<ANCHOR>...</ANCHOR>`. It must emit only physically visible traits — species, skin,
hair, eyes, build, habitual clothing — and must exclude personality, plot, camera
language, and art style. Target 10-14 comma tokens.

Scene anchor derives the same way from `scene.description` + `scene.context` and must
name location type, genre, and dominant palette. Target 8-10 tokens. For this scene it
would produce roughly `starship interior, deep space, sci-fi, worn metal panels, cool
blue screen glow` — which alone kills the mountains.

Because step 2 writes back, the second image onward is fully deterministic and the
value is human-editable.

### D2 — Deterministic assembly, no node-graph surgery

Prompt assembly runs through a 140-node graph
(`agents/visual/modules/generate-visual-asset.json`). Do not edit it.

`StyleMixin.apply_styles` (`style.py:160`) is a single Python choke point, called from
the `ApplyStyles` node (`nodes.py:347-350`) **after** the LLM's parts are already in
`prompt.parts`. Everything below happens there.

Insertion order for the positive prompt:

```
art style tokens          (existing, index 0)
vis_type style tokens     (existing, index 1)
scene anchor              (new)
character anchors, in-frame only, each followed by that character's visual_rules
LLM action keywords       (existing, sanitised — see D4)
```

`VisualPrompt._build_prompt` dedupes with `dict.fromkeys` (`schema.py:168`), which
keeps first occurrence. Anchors inserted ahead of the LLM part therefore win position
and absorb any duplicate the LLM emitted. This is why order matters.

### D3 — In-frame character selection

Anchors are emitted only for characters actually in the shot, not every character in
the scene.

The LLM already produces `positive_descriptive` — the prose the review UI labels
`Instructions` — and it names who is present. Match scene character names against
that text, case-insensitive, on word boundaries, handling first-name-only mentions and
names containing spaces or apostrophes.

- No descriptive part available: fall back to all active characters.
- More than 3 matched: keep the 3 most-mentioned and log the drop. A safety valve
  against CLIP overflow, not a general cap.

### D4 — Sanitise the LLM keyword part

Applied to the LLM part only. Never to anchors or styles.

Two categories, both predictable because our own template wording generates them:

- **Format/camera meta**: `horizontal`, `landscape`, `portrait format`, `square`,
  `cinematic framing`, `dynamic composition`, `moment-capturing`, `screen cap`,
  `storyboard frame`
- **Non-visual abstractions**: `tension`, `tense moment`, `emotion`, `action`,
  `interaction`, `corruption`, `diagnostic`, `waiting`, `watching`, `focused`,
  `moment`

Dropped tokens get logged so the list can be tuned against real output. The blocklist
is the cure; D5 is the prevention.

### D5 — Rewrite the templates

`generate-image-SCENE_ILLUSTRATION.jinja2`:

- Delete line 52 ("Make sure your prompt emphasizes the horizontal/landscape
  format...") and the framing/orientation requirement and avoid-list lines. Resolution
  enforces format; restating it only pollutes the prompt.
- Delete the per-character appearance RAG queries (lines 6 and 11). Appearance now
  comes from anchors. This also removes N LLM calls per image.
- Keep the scene-action query. Add an explicit rule: name the concrete location and
  genre; emit only physically visible nouns; no plot, state, or emotion words.

`generate-image-prompt-type.jinja2`: add the visible-nouns-only constraint and state
the token budget.

### D6 — Prompt token cap

New. `prompt_generation.max_length` (`agent.py:167`, value 1024) is the **LLM's**
generation budget, not an image-prompt cap — there is no existing cap to reuse.

Add a CLIP-token cap for the assembled positive prompt. Truncation order when over
budget: LLM action keywords first, then character anchors beyond the first, then the
scene anchor. Styles and the first character anchor are never truncated.

### D7 — Seed

`SamplerSettings` (`schema.py:238`) currently holds `steps` only, and the A1111 payload
(`backends/automatic1111.py:134-143`) sends no seed, so every image reseeds.

Add `SamplerSettings.seed: int | None`, pass it through, and add a config option
`seed_mode` = `random` (default, unchanged behaviour) | `scene` (derived from scene id)
| `fixed`.

**Sizing this honestly**: in txt2img a seed applies to the whole image, not per
character. Pinning it makes palette and rendering style consistent across
illustrations. It does **not** make two crew members the same people shot to shot —
that is D1's job. This is a style lever.

### D8 — UI

Both anchors editable, plus a Derive button.

- Character: extend `WorldStateManagerCharacterVisualsRules.vue` (the only frontend
  file touching `visual_rules`), with a websocket action
  `update_character_visual_anchor` mirroring `handle_update_character_visual_rules`
  (`server/world_state_manager/character.py:121`).
- Scene: anchor field beside the existing visual style template control.
- Derive button regenerates the anchor from prose and overwrites the field.

## Acceptance criteria

Verified end-to-end through Playwright against the running app on `localhost:8082`,
review queue at
`#/s/infinity-quest-dynamic-story-v2/main?save=Infinity+Quest+1.json&visual=review`.

1. **AC1 — Identity is byte-stable.** Two consecutive `SCENE_ILLUSTRATION`
   generations in the same scene produce prompts whose character-anchor substring is
   byte-identical.
2. **AC2 — Setting present.** Every prompt contains scene-anchor tokens naming the
   location type and genre. The mountains failure cannot recur.
3. **AC3 — No meta tokens.** No D4 blocklist entry appears in any final prompt.
4. **AC4 — `visual_rules` honoured.** Elmer's face-in-shadow rule appears in every
   prompt where Elmer is in frame.
5. **AC5 — Off-screen characters excluded.** A character absent from the descriptive
   text contributes no anchor.
6. **AC6 — Budget respected.** Assembled positive prompt stays within the D6 cap;
   truncation follows the stated order.
7. **AC7 — UI round-trip.** Edit an anchor in the world editor, save, reload the page,
   value persists, and the next generation uses the edited value.
8. **AC8 — Seed.** With `seed_mode: scene`, two generations report the same seed in
   asset metadata; with `random`, they differ.
9. **AC9 — Playwright evidence.** Generate, open the review queue, screenshot, and
   assert on the visible Prompt text for AC2, AC3, AC4.

## Non-goals

- Reference-image consistency (IP-Adapter, ControlNet, `IMAGE_EDIT` with the character
  card as reference). Strongest lever available, deliberately deferred — needs a
  second backend running, and `backend_image_edit` is empty in `config.yaml`.
- Per-character LoRA training.
- Upstream-merge-friendly file layout. Changes go in place; this fork is not tracking
  upstream closely.
- Moving the repo off the `zakhodgsonamauk` account. Separate job.

## Hazards — established during investigation, do not rediscover

- `StyleMixin.apply_style` (singular, `style.py:150`) is **broken**. It passes a
  template-id string to `style_template(vis_type: VIS_TYPE)`, which calls
  `vis_type.value.lower()` — `AttributeError` on a `str`. Its only caller is the
  `ApplyStyle` node (`nodes.py:382`), which no shipped module uses. Build on
  `apply_styles` (plural). Fixing or deleting `apply_style` is optional cleanup, not
  part of this work.
- Prompt assembly is a node graph, not a function. Hand-editing
  `generate-visual-asset.json` is high-risk; every change here has a Python choke
  point available instead.
- `prompt_generation.max_length` is the LLM budget, not an image-prompt cap. Do not
  overload it — add a separate cap (D6).
- `_build_prompt` dedupe keeps first occurrence (`schema.py:168`). Insertion order is
  load-bearing, not cosmetic.
- `backend_image_edit: ''` in `config.yaml` — the `IMAGE_EDIT` path is unavailable.
  Do not route tests through it.
- Character names contain spaces and apostrophes. The in-frame matcher needs proper
  escaping.
- Generated images and `scenes/*/assets/library.json` churn are disposable test
  output. Do not spend effort preserving them; do not blind-revert them either.
- The `visual=review` URL parameter is the review-queue deep link and works today —
  Playwright can go straight there.
