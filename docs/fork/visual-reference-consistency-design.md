# Visual reference consistency — design

**Status**: implemented; mechanism verified directly against ComfyUI. Not yet verified
end-to-end through a running Talemate scene — see *Verification* below.
**Date**: 2026-07-30
**Applies to**: `SCENE_ILLUSTRATION` first. Other vis types are excluded deliberately.
**Follows**: [visual-consistency-design.md](visual-consistency-design.md) (anchors) and
[visual-anchor-freshness-design.md](visual-anchor-freshness-design.md) (wardrobe).
**Setup**: [comfyui-ipadapter-setup.md](comfyui-ipadapter-setup.md).

## Problem

Generated illustrations of Kaira do not look like Kaira. This is the same complaint the
anchor track addressed, and that track worked — prompts are now stable, correctly
ordered and setting-anchored. The images still show a different woman every time.

### Evidence

Three images of the same character from `infinity-quest-dynamic-story-v2`:

| Asset | How made | What it shows |
|---|---|---|
| `5d1b53ff…` canonical card | TEXT_TO_IMAGE | Pale lavender skin, short curly hair, amber eyes, purple dress |
| `2a2d07de…` cover | IMAGE_EDIT | Purple skin, long lavender hair, blue eyes, facial markings, bodysuit |
| `dc8eafc5…` illustration | TEXT_TO_IMAGE | **Human** skin, blue hair, underwear — and a violet alien face rendered on a monitor |

Her prose says "deep violet skin with faint geometric patterns… large dark eyes, no
visible iris… four-fingered hands… fitted utility suit". None of the three match it, and
none match each other. There is no canonical Kaira for anything to be consistent *with*.

The illustration's prompt was not at fault. It contained `violet skin`, `geometric
patterns`, `fitted dark blue-grey utility suit` and both characters' anchors — the
anchor machinery did its job, and the checkpoint ignored it.

### The load-bearing measurement

A control generation on this machine, with the failing prompt strengthened to
`(deep violet skin:1.5)` and `human skin, ordinary skin tone` in the negatives, on
`CyberRealisticPony_V9`:

> Result: a human-skinned woman with blue hair.

Text cannot fix this. An anchor makes a prompt **stable**; it cannot make it
**specific**. "deep violet skin, indigo hair, large dark eyes" describes a *type*, and a
diffusion model samples a fresh instance of that type on every generation. The previous
design's closing note said exactly this — "prompt-assembly correctness is not rendering
fidelity" — and listed reference-image conditioning as the "strongest lever available,
deliberately deferred". This track picks it up.

## Design

### D1 — Reference conditioning via ComfyUI + IP-Adapter

The generation is conditioned on the character's card **image**. IP-Adapter PLUS (SDXL)
encodes the reference with CLIP-Vision and injects it into cross-attention, so identity
comes from pixels rather than words.

Why this and not the alternatives, given the constraints (single 16GB GPU, explicit
content a hard requirement, no cloud egress):

- **InstantID / PuLID / IP-Adapter FaceID** run InsightFace. It expects a human face and
  normalises away the traits that make a non-human character recognisable. Rejected.
- **Flux Kontext / Qwen-Image-Edit** (`qwen_image_edit.json` already ships here) are the
  strongest editors but the weakest on explicit content. Rejected on the hard requirement.
- **Cloud reference editing** (Gemini 3 Pro Image, 3 references, already configured in
  `config.yaml`) would refuse this scene material, and sends story content off-machine.
- **Per-character LoRA** remains the highest-fidelity option and is a non-goal here: it
  needs a training rig and curated data per character.

### D2 — Existing plumbing, one new selection step

Almost everything needed already exists in this fork, unused:

- `GenerationRequest.reference_assets` and `reference_bytes`
- `SelectBackend` (`nodes.py:436`) routes to `GEN_TYPE.IMAGE_EDIT` when references are
  present and an edit backend is available
- the ComfyUI backend uploads references to `/upload/image` and binds them to nodes
  titled `Talemate Reference N`, disconnecting any that go unpopulated
- `backend_image_edit.max_references` reports how many slots the selected workflow has

The gap was that **nothing ever populated `reference_assets`** for an illustration. The
director module passes `[]`, so the IMAGE_EDIT path only ever ran when a user edited an
image by hand.

`ReferenceMixin.attach_character_references` (`references.py`) fills that gap:

1. bail unless `_references.enabled`, the vis type is `SCENE_ILLUSTRATION`, and
   `can_edit_images`
2. never override a caller-supplied reference — a user editing an image chose it
3. find the characters **named in the assembled prompt**, most-mentioned first
4. take each one's `cover_image`, falling back to a stored `CHARACTER_CARD`
5. fill up to `max_references` slots, then set `gen_type = IMAGE_EDIT`

Called at the top of `GenerationMixin.generate`, **before** `_finalize_prompt`, because
that method picks its keyword handling from whichever backend the request is bound for
and attaching a reference is what changes that binding.

In-frame detection reuses `_mention_count` and the same evidence as
`_drop_absent_character_anchors`: the LLM's own keywords name who is in the shot. The
character who keeps their anchor is therefore the character who gets a reference. Python
choke point, not node-graph surgery — the hazard list in the previous design still applies.

### D3 — The workflow

`templates/comfyui-workflows/sdxl-ipadapter-character.json`. Binding is by node title, so
no backend code changes:

```
Talemate Load Checkpoint ─┬─────────────────────────────► IPAdapterAdvanced ──► KSamplerAdvanced
                          ├─► Talemate Positive Prompt ───────────────────────►
                          ├─► Talemate Negative Prompt ───────────────────────►
                          └─► VAE ────────────────────────────────────────────► VAEDecode
Talemate Reference 1 (LoadImage) ─► IPAdapterAdvanced
IPAdapterModelLoader / CLIPVisionLoader ─► IPAdapterAdvanced
Talemate Resolution (EmptyLatentImage) ─► KSamplerAdvanced
```

Exactly **one** reference node, so `max_references` is 1 and the most-mentioned character
in the shot wins the slot. Adding `Talemate Reference 2` raises the cap with no code change.

### D4 — Conditioning parameters, measured not guessed

Shipped defaults: `weight 0.8`, `weight_type "ease out"`, `end_at 0.8`,
`embeds_scaling "K+V"`, `combine_embeds "concat"`.

The full measurement grid is in the setup guide. Two findings shaped the defaults:

- **The weight must decay.** IP-Adapter transfers the reference's *composition* along
  with its identity. Held constant to the final step, it reproduced the reference's
  framing — a portrait instead of a scene — and duplicated the subject, twice at
  weight 0.85 and four times under `style transfer` at 1.0. `ease out` with `end_at 0.8`
  leaves the last steps to the prompt, which is what keeps the room and the action.
- **`K+V` beat raising the weight.** At `V only` the skin colour normalised back to human
  by the end of sampling. Conditioning both halves of attention held the colouring at a
  weight low enough to avoid duplication.

### D5 — Configuration

`_references.enabled` defaults to **off**. It does nothing without a reference-capable
edit backend, and silently doing nothing is worse than an explicit switch.

Two existing settings must change for this path, and both are counter-intuitive:

| Setting | Value | Why |
|---|---|---|
| `comfyui_image_edit.prompt_type` | `KEYWORDS` (was `DESCRIPTIVE`) | the prompt is still a scene description, not an edit instruction |
| `prompt_generation.revise_edit_prompts` | `false` (was `true`) | it rewrites the prompt into an edit instruction ("change her pose to crossed arms"), discarding the scene |

The IMAGE_EDIT path was built for editing. Here we are reusing it as
"generate, conditioned on a reference", so both editing-shaped defaults are wrong.

## Verification

**Verified directly against ComfyUI** (`localhost:8188`, same seed, same prompt, prompt
naming the character):

1. **AC1 — the failure reproduces without a reference.** Control generation: human skin,
   blue hair. Matches the reported symptom.
2. **AC2 — identity transfers with one.** Violet skin, lavender hair, facial markings and
   the bodysuit all present, from her cover image alone.
3. **AC3 — identity is stable across seeds.** Three different seeds produced recognisably
   the same person in three different rooms.
4. **AC4 — the scene survives.** Starship interior with consoles and viewports retained,
   single subject, at the shipped defaults.

**Verified by unit test** (`tests/test_visual_reference.py`, 12 tests): attachment and
IMAGE_EDIT routing; cover-image preference with card fallback; and every bail-out —
feature off, caller-supplied reference, `CHARACTER_CARD` vis type, no edit backend, zero
reference slots, character absent from the prompt, character with no asset. Plus cap
enforcement and stability of the pick across repeated calls.

**Not verified**: an end-to-end generation triggered from a loaded scene in the running
app. The wiring is exercised only by unit tests; the rendering is exercised only through
ComfyUI directly. Do that before trusting this in play.

## Amendments — added after the first pass

Three changes, all driven by measurements rather than reading. Read these before trusting
D2 or the *Known limitations* section as originally written.

### A1 — CHARACTER_CARD is now referenced too (amends D2)

The original reasoning was that the card *is* the reference, so conditioning it on itself
would freeze its pose. True, but it was the wrong trade: cards generated with no reference
came back as a different woman every time — two generated in the app on 2026-07-30 (18:35
and 19:08) shared neither face, skin tone nor hair with each other or with the cover, and
one placed her in front of a desktop monitor.

`REFERENCE_VIS_TYPES` now includes `CHARACTER_CARD`. Verified: a card generated with the
cover as reference is recognisably the same character, on a plain backdrop, in a neutral
standing pose. Inheriting the cover's framing is the smaller cost, and for a *replacement*
card it is the point.

`request.character_name` now takes priority over prompt mentions when choosing the subject,
because a card request carries it explicitly.

### A2 — references are one subject, many pictures (amends D2/D3)

The first implementation filled spare reference slots with *different characters* — Kaira
then Elmer. That is wrong in principle: averaging two people's embeddings yields a third
person. Slots now take up to N images of the single subject, and when only one exists it is
repeated to fill every slot.

Repeating is not cosmetic. `Workflow.set_reference_images` disconnects unpopulated
reference nodes, and the batch node feeding IPAdapter fails ComfyUI validation when one of
its inputs has been deleted. Filling every slot keeps a one-picture character working.

**Curation is required, not optional.** Averaging the cover with two off-spec cards
measurably degraded the result — violet skin washed toward human, markings lost. Extra
references are therefore opt-in via the `reference` asset tag; untagged cards are used only
when there is no cover.

### A3 — a second workflow that cannot hijack composition

`sdxl-ipadapter-inpaint.json`: pass 1 generates the scene with no reference at all, the
person is segmented (`BodySegment`), and pass 2 regenerates only inside that mask with the
reference applied — `SetLatentNoiseMask` on the latent and `attn_mask` on IPAdapter, so
identity is confined to the person's pixels.

This is the structural fix for composition leakage: the reference cannot move the camera or
duplicate the subject if it only reaches pixels the subject already occupies. Measured
against the single-pass workflow on the same prompt: a genuine cluttered working interior
and a natural walking pose, instead of a bare room and a pin-up stance.

Cost: a faint outline glow, a slightly plastic surface, and roughly double the generation
time. Parameter sweep and the shipped values are in the setup guide. `FeatherMask` was
tried first and removed — it feathers image edges, not mask silhouettes.

## Known limitations

- **The reference is now the canonical character, and Kaira's is off-spec.** Her cover is
  pale lavender where her prose says deep violet, and it is a glamour portrait — so
  generated images inherit glamour posing and ignore "standing at the console". The
  mechanism faithfully reproduces whatever the cover is. Fixing the cover is the highest
  -value next action, and it is a curation job, not a code one.
- **Cropping the reference makes it worse.** A head-and-shoulders crop transferred its own
  close-up framing, producing portraits instead of scenes. References should be full-body,
  neutrally posed, plainly lit, in the character's usual clothing.
- **Wardrobe fights the reference.** At these weights the reference's clothing wins over
  prompt clothing keywords. `_freshness` (wardrobe tracking) is currently disabled in
  `config.yaml`, so nothing is updating what she is described as wearing either.
- **One referenced character per image.** Both workflows expose three slots, but they all
  serve the *same* subject (see A2). Elmer sharing a frame with Kaira gets no reference and
  stays text-only. Per-character regional conditioning is a separate job.
- **The inpaint workflow leaves a faint halo** around the figure and a slightly plastic
  surface, and costs ~2× the time. Tunable, not solved.
- **Seeds are not honoured on this backend.** `Workflow.set_seeds` (`comfyui.py:211`)
  randomises every `seed`/`noise_seed` field on every run, so `seed_mode: SCENE` has no
  effect through ComfyUI. Pre-existing, out of scope, worth fixing separately.
- **One GPU, one backend.** ComfyUI and KoboldCpp cannot both hold VRAM alongside an
  Ollama text model on 16GB. `start-fork.bat USE_COMFYUI=1` picks one.

## Non-goals

- Per-character LoRA training.
- References for `CHARACTER_CARD` — the card *is* the reference; conditioning it on itself
  would freeze whatever pose it happens to be in.
- Multi-character reference composition (masked regions per character).
- Fixing `set_seeds`, or the ComfyUI backend's seed handling generally.

## Hazards — established during this work, do not rediscover

- **`robocopy /XD models` destroys a Python install.** Bare-name exclusions match *every*
  directory of that name, including `comfy\ldm\models` and `torchvision\models`. It
  surfaces later as unrelated-looking import errors. Use full paths.
- **robocopy from Git Bash fails**: MSYS rewrites `/E` to `E:/`.
- **`strip_emphasis` was used in `generation.py` without being imported.** Every
  KEYWORDS-mode finalise raised `NameError`; 31 anchor tests were failing in the working
  tree before this track started. Fixed here — check imports when tests fail unexpectedly.
- **Windows reports a stale file size for open files.** A download in progress can read
  `0 MB` for minutes. Check `WriteTransferCount` on the process instead.
- **`search_assets` returns asset *ids*, not asset objects.**
- **pytest-asyncio contexts**: an `active_scene` token set inside a test cannot be reset
  from a sync fixture's teardown — it raises `ValueError`. Guard it.
- The bundled `default-sdxl.json` names a checkpoint (`protovisionXL…`) that does not
  exist locally, and `set_main_model` no-ops on an empty config value — so the ComfyUI
  model setting must name a real file or generation fails at the loader.
