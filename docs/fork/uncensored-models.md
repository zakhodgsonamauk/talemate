# Uncensored model stack

The models currently wired into this fork, why they were chosen, and the settings
each one needs. Companion to
[Images without ComfyUI](images-without-comfyui.md), which covers the KoboldCpp
plumbing itself.

Everything here runs locally. Nothing leaves the machine.

---

## What's active

| Role | Model | Size | Served by |
|---|---|---|---|
| **Text** | `hf.co/TheDrummer/Rocinante-X-12B-v1-GGUF:Q4_K_M` | 6.96 GB | Ollama `:11434` |
| **Images** | `CyberRealistic_PonySemi_V5.safetensors` | 6.62 GB | KoboldCpp `:5001` |
| Embeddings | `all-MiniLM-L6-v2` (CPU) | 90 MB | sentence-transformers, in-process |

Replaced `qwen3:8b-q4_K_M` (text) and `sd_xl_turbo_1.0_fp16` (images).
`CyberRealisticPony_V9.0_FP16` (fully photoreal) is also downloaded and available —
see [Alternative image checkpoints](#alternative-image-checkpoints).

**Art style must match the checkpoint.** Each image model has a paired visual style
in `templates/world-state/fork-styles.yaml`:

| Checkpoint | Art style | Why |
|---|---|---|
| `CyberRealistic_PonySemi_V5` | `fork_styles__semireal_pony` | No illustration/painting negatives — those fight the model's own painterly finish |
| `CyberRealisticPony_V9.0_FP16` | `fork_styles__photoreal_pony` | Negative-prompts illustration/painting to push toward photography |

Using the photoreal style with the semi-real checkpoint half-cancels the model's
aesthetic. Switch both together.

---

## Why the previous models resisted

Talemate **already** asks for latitude. `ClientBase.decensor_enabled = True`
(`src/talemate/client/base.py:292`) selects the `*_decensor` system prompts, and
`conversation/system.jinja2` appends:

> *Writers may use strong or explicit language when it serves the story's tone and
> their character's voice. They will never remind us that what they write is
> fictional.*

That's a soft nudge, not a jailbreak — and `qwen3:8b` is a **reasoning** model, so
it tends to re-assert its alignment inside its own thinking block regardless. The
fix is the model, not more prompt engineering. Don't bother fighting the templates.

---

## Text — Rocinante-X 12B

TheDrummer's Mistral-Nemo 12B roleplay finetune. Chosen over the alternatives
because:

- Genuinely uncensored for creative fiction, without the prose damage that crude
  abliteration often causes — it's a finetune, not just an ablation.
- **Non-reasoning**, so no mid-thought alignment relapse.
- 6.96 GB leaves room for image generation on a 16 GB card (see VRAM below).
- Measured **~76 tok/s** on an RTX 4080 at 8192 ctx.

### Prompt template — this one matters

The model card says **Mistral v3 Tekken, *not* v7**, and explicitly *"REMOVE
`[SYSTEM_PROMPT]`"*. In Talemate's template set that means:

| Template | Content | Use? |
|---|---|---|
| `Mistral.jinja2` | `[INST] {{system}} {{user}} [/INST]` | ✅ **this one** |
| `MistralV7Tekken.jinja2` | `[SYSTEM_PROMPT]…[/SYSTEM_PROMPT][INST]…` | ❌ wrong format |

Talemate stores the choice as a **copy of the template named after the model**, in
`templates/llm-prompt/user/`. The filename rule is
`model.replace("/", "__").replace(":", "_")`
(`src/talemate/client/model_prompts.py:206`), so for this model:

```
templates/llm-prompt/user/hf.co__TheDrummer__Rocinante-X-12B-v1-GGUF_Q4_K_M.jinja2
```

Verify Talemate resolves it:

```powershell
.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'src'); from talemate.client.model_prompts import model_prompt; print(model_prompt('hf.co/TheDrummer/Rocinante-X-12B-v1-GGUF:Q4_K_M','SYS','USR<|BOT|>C')[0])"
# -> [INST] SYS USR [/INST] C
```

If you ever see `default.jinja2` in the client settings, the mapping file is
missing and output quality will noticeably drop.

### Pulling it

Ollama can pull any GGUF straight from HuggingFace, no Ollama-library entry needed:

```powershell
ollama pull hf.co/TheDrummer/Rocinante-X-12B-v1-GGUF:Q4_K_M
```

!!! warning "Expect a long verify step"
    The blob downloads quickly, then Ollama runs a sha256 verify. Because
    `OLLAMA_MODELS` lives on the Dropbox drive, verifying 7.5 GB takes minutes and
    prints no progress. It looks hung. It isn't — leave it. Until the manifest is
    written the model won't appear in `ollama list`.

---

## Images — CyberRealisticPony V9

Pony-based photoreal checkpoint, heavily NSFW-trained. Single-file safetensors from
`cyberdelia/CyberRealisticPony` on HuggingFace — no auth needed.

!!! tip "Why not Civitai, and why not the John6666 mirrors"
    Civitai requires an API token for downloads. The `John6666/*` HF mirrors are
    convenient but are **diffusers-format directories** with no single-file
    checkpoint at the repo root, so KoboldCpp cannot load them. Look for a repo
    with a root-level `.safetensors`.

### Required settings

Per the model card — and these differ completely from SDXL Turbo's:

| Setting | Turbo (old) | **Pony (now)** |
|---|---|---|
| Steps | 6 | **30** |
| CFG Scale | 1.0 | **5** |
| Sampling Method | Euler a | **DPM++ 2M** |
| Schedule Type | Automatic | **Karras** |
| Prompting Type | Keywords | Keywords |

VAE is baked in — no `--sdvae` needed.

### Score tags — handled by a visual style

Pony checkpoints expect `score_9, score_8_up, score_7_up, …` at the front of the
prompt. Talemate builds prompts from scene context and won't add those itself, so
they live in a **visual style template** instead of being hardcoded anywhere.

Defined in **`templates/world-state/fork-styles.yaml`** — a new group file, *not* an
edit to upstream's `visual-styles.yaml`.

That distinction matters: `templates/world-state/*.yaml` is listed in `.gitignore`,
but `visual-styles.yaml` was committed by upstream *before* that rule was added, and
gitignore does not untrack an already-tracked file. Editing it would therefore be a
real upstream diff. A **new** `.yaml` in the same directory genuinely is ignored
(`git check-ignore` confirms), so the style costs zero upstream diff.

The loader treats one YAML file as one group, keyed by its top-level `uid`
(`src/talemate/world_state/templates/base.py:124-166`), so a separate group with its
own uid slots in cleanly:

```yaml
uid: fork_styles
name: Fork Styles
templates:
  photoreal_pony:
    name: Photoreal (Pony)
    group: fork_styles
    uid: photoreal_pony
    template_type: visual_style
    visual_type: STYLE
    positive_keywords: [score_9, score_8_up, score_7_up, photorealistic,
      RAW photo, detailed skin texture, cinematic lighting, sharp focus]
    negative_keywords: [text, watermark, low quality, blurry, anime, cartoon,
      illustration, drawing, painting, 3d render, deformed, extra limbs, bad hands]
```

Selected via Visualizer → Styles → **Art Style**, stored as
`agents.visual.actions._styles.config.art_style = fork_styles__photoreal_pony`.
`StyleMixin.apply_style` inserts the style part at index 0
(`src/talemate/agents/visual/style.py:157`), so the score tags always lead the
prompt — which is what Pony wants.

To go back to SDXL Turbo, switch this to `visual_styles__digital_art` **and**
restore the Turbo sampler settings. The two are a matched pair.

!!! warning "`templates/world-state/*.yaml` is a trap"
    The gitignore rule suggests everything there is user data, but
    `visual-styles.yaml`, `talemate/default.yaml` and `talemate/human.yaml` are all
    **tracked**. Check `git ls-files templates/world-state/` before editing anything
    in that directory — new files are ignored, existing ones are not.

---

## VRAM on a 16 GB card

Measured, not estimated:

| State | VRAM |
|---|---|
| Desktop only | ~2.5 GB |
| KoboldCpp idle | +1.5 GB |
| Rocinante-X resident @ 8192 ctx | +~8 GB |
| Image weights during a generation | +~5.5 GB |

KoboldCpp loads the diffusion weights **per request** and frees them after, so the
~5.5 GB is transient — it isn't held between generations. Peak lands near 15 GB of
16 GB, which works but has little headroom.

If you hit OOM or thrashing:

- add `--sdquant 2` to the KoboldCpp launch (loads the image model at q4, roughly
  halving its footprint);
- drop the Ollama client's Context Length from 8192 to 4096;
- or lower `OLLAMA_KEEP_ALIVE` so the text model unloads between turns (costs a
  reload each turn).

---

## Measured performance

RTX 4080 16 GB, Windows 11, KoboldCpp 1.117.1.

| Operation | Time |
|---|---|
| Text generation | ~76 tok/s |
| Image, 1216×832, 30 steps | ~20 s |
| *(previously: SDXL Turbo, 6 steps)* | *~14 s* |

Only ~6 s slower per image than Turbo despite 5× the steps — much better than the
30–60 s a non-Turbo checkpoint would normally imply.

---

## Sampler settings — required for Mistral-Nemo

Talemate's `narrate` prompts map to the **`creative`** inference preset, which is a
bare `InferenceParameters()`: `temperature 1.0, top_p 1.0, top_k 0`, with only
`min_p 0.1` constraining it (`src/talemate/config/schema.py:362, 402`).

Mistral-Nemo is unusually temperature-sensitive — its own model card recommends
0.3. At 1.0 it degenerates. Observed in a real session: the narrator returned

```
后汉书后汉书后汉书后汉书后汉书后汉书后汉书…
```

repeated ~30 times instead of prose. Same template, same model, a different
generation moments earlier was fine — so it's intermittent, which makes it easy to
dismiss as a fluke. It isn't.

Fixed in `config.yaml` under `presets.inference.creative`:

| Parameter | Default | Set to |
|---|---|---|
| `temperature` | 1.0 | **0.8** |
| `repetition_penalty` | 1.0 | **1.05** |

`changed: true` is required or `save_config()` drops the preset on write
(`config/state.py:103-105` only persists presets flagged as changed).

Verify what a given prompt kind will actually use:

```powershell
.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'src'); from talemate.config import get_config; from talemate.client.presets import preset_for_kind
class F: preset_group=''
get_config(); print(preset_for_kind('narrate_512', F()))"
# -> temperature 0.8 ... repetition_penalty 1.05
```

If narration still degenerates, drop `creative` to 0.7 before suspecting anything
else.

!!! danger "Edit config.yaml only while the backend is stopped"
    `get_config()` caches the config in a module-level global and **never re-reads
    the file** (`src/talemate/config/state.py:33-37`). The backend also calls
    `save_config()` during normal play — including on autosave — which writes its
    in-memory state over the file.

    So editing `config.yaml` against a running backend achieves nothing and gets
    silently reverted the next time it saves. Stop the backend, edit, restart.

## Practical notes

**The image model leans explicit.** A test prompt describing only *"a weathered
mercenary woman seated at a table"* returned nudity unprompted. For establishing
shots and scenery, add terms like `nude, nsfw, topless` to the **negative** prompt
(or make a second visual style for SFW scene-setting and switch per shot). Left as
a per-scene choice rather than baked into the style.

**Switching models is two coordinated changes.** The checkpoint *and* the sampler
settings *and* the art style must move together. `start-fork.local.bat` holds the
active `KCPP_MODEL` with the alternative commented out directly above it.

**A restart is required to change image model.** KoboldCpp takes one `--sdmodel`
per launch. The text model, by contrast, is a live config change in Talemate.

**Don't put model files in a synced folder.** `OLLAMA_MODELS` points into Dropbox,
so every `ollama pull` triggers a multi-GB upload. Pulling the 7.5 GB Rocinante blob
left Dropbox uploading at ~1.5 MB/s, which starved a concurrent HuggingFace download
down to ~0.4 MB/s — a 6.6 GB checkpoint went from ~30 min to an estimated ~130 min.
Diagnose with:

```powershell
Get-Counter '\Network Interface(*)\Bytes Sent/sec','\Network Interface(*)\Bytes Received/sec' -SampleInterval 2 -MaxSamples 2
```

Sustained upload with starved download means sync contention, not a slow mirror.
Excluding the Ollama model directory from Dropbox selective sync would fix this
permanently — 254 GB of GGUF blobs has no business being synced.

**Use `hf_transfer` for multi-GB checkpoints.** HuggingFace throttles per
connection, so a single-stream `curl` is slow regardless of your link. Measured on
the same 6.6 GB file, same session:

| Method | Rate | Wall time |
|---|---|---|
| `curl -L -o …` | 0.9 MB/s | ~130 min (est.) |
| `hf_hub_download` + `hf_transfer` | **11.0 MB/s** | **10.3 min** |

```powershell
.venv\Scripts\python.exe -m pip install hf_transfer   # one-off, ~1 MB
$env:HF_HUB_ENABLE_HF_TRANSFER='1'
.venv\Scripts\python.exe -c "import os; os.environ['HF_HUB_ENABLE_HF_TRANSFER']='1'
from huggingface_hub import hf_hub_download
print(hf_hub_download(repo_id='cyberdelia/CyberRealisticSemiRealPony',
                      filename='CyberRealistic_PonySemi_V5.safetensors',
                      local_dir='.'))"
```

`ollama pull` already parallelises, which is why the 7.5 GB text model came down
quickly while the curl'd checkpoint crawled.

## Alternative image checkpoints

Same uploader, same repo layout (single-file `.safetensors` at the repo root, no
auth), so these are drop-in — only `KCPP_MODEL` changes:

| Model | Style | Repo | Paired art style |
|---|---|---|---|
| `CyberRealistic_PonySemi_V5` | Semi-realistic *(active)* | `cyberdelia/CyberRealisticSemiRealPony` | `fork_styles__semireal_pony` |
| `CyberRealisticPony_V9.0_FP16` | Photoreal | `cyberdelia/CyberRealisticPony` | `fork_styles__photoreal_pony` |

Both are Pony-based, so the score tags and sampler settings carry over unchanged —
only the checkpoint and its paired art style change. Anime-style alternatives that
also ship a root-level single file
include `Ine007/waiIllustriousSDXL_v160` and
`LyliaEngine/autismmixSDXL_autismmixConfetti` — but those are Illustrious/NoobAI
family and want booru-style tags rather than Pony `score_*` tags, so they'd need
their own visual style.
