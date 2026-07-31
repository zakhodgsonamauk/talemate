# Character consistency with ComfyUI + IP-Adapter

Fork-specific setup guide. Gets **the same character's face and colouring** into every
scene illustration, by conditioning generation on their card image instead of on words.

Read [visual-reference-consistency-design.md](visual-reference-consistency-design.md)
for *why* words cannot do this. The short version: an appearance anchor describes a
type, and an SDXL checkpoint samples a fresh person from a description every time. On
this machine a prompt containing `(deep violet skin:1.5)` — with `human skin` in the
negatives — still produced a human-skinned woman. Identity needs pixels.

Every step below is a command with a check you can run. Measured on Windows 11, RTX 4080
(16GB), on 2026-07-30.

---

## What you end up with

- A **second, dedicated ComfyUI install** at `C:\ComfyUI-talemate`, duplicated from the
  known-good `ComfyUI-2` rig so that rig is never modified.
- IP-Adapter PLUS (SDXL) + CLIP-Vision ViT-H, driving identity from a reference image.
- The **uncensored** `CyberRealisticPony_V9` checkpoint reused in place from the
  KoboldCpp models folder — no second 6.5GB copy, and explicit content still works.
- KoboldCpp retained as the fallback text-to-image path.

!!! warning "One GPU, one image backend at a time"
    ComfyUI with SDXL + IP-Adapter holds ~8-10GB; KoboldCpp holds ~7GB; an Ollama text
    model wants ~5GB. On 16GB you can run ComfyUI **or** KoboldCpp alongside Ollama, not
    both. `start-fork.bat` picks one — see step 7.

---

## 1. Duplicate the working ComfyUI

The existing rig at `I:\Dropbox (Personal)\Projects\ComfyUI-2` already solves the hard
Windows problems (embedded Python 3.12.10, torch 2.7.1+cu128, working CUDA). Copying it
inherits all of that. It is **not** modified by any step here.

```powershell
$src  = "I:\Dropbox (Personal)\Projects\ComfyUI-2\ComfyUI-Easy-Install"
$dest = "C:\ComfyUI-talemate"
robocopy $src $dest /E /XD "$src\ComfyUI\models" "$src\ComfyUI\output" `
    "$src\ComfyUI\input" "$src\ComfyUI\.git" /R:1 /W:1 /MT:16 /NFL /NDL /NP
```

Roughly 14GB and 67,000 files; a few minutes. **Exit code 3 is success** (1 = files
copied, 2 = extras present); only ≥8 is a failure.

!!! danger "Do not exclude directories by bare name"
    `/XD models` looks harmless and silently destroys the install: it excludes **every**
    directory named `models`, including `comfy\ldm\models` and
    `site-packages\torchvision\models`. The failure surfaces much later as
    `ModuleNotFoundError: No module named 'comfy.ldm.models'`, then as
    `ImportError: cannot import name 'models' from partially initialized module
    'torchvision'`. Always give `/XD` the **full paths**, as above.

!!! danger "Run robocopy from PowerShell, not Git Bash"
    Git Bash's MSYS path translation rewrites `/E` into `E:/`, and robocopy fails with
    `ERROR : Invalid Parameter #3 : "E:/"`.

Why C: and not beside the original? The models are large and the original lives in
Dropbox; a second copy there would sync tens of gigabytes. C: keeps it out of sync.

**Check:**

```powershell
Test-Path C:\ComfyUI-talemate\ComfyUI\comfy\ldm\models\autoencoder.py           # True
Test-Path C:\ComfyUI-talemate\python_embeded\Lib\site-packages\torchvision\models\__init__.py  # True
```

Both must be `True`. If either is `False`, the bare-name exclusion bit you — re-run the
robocopy above; it fills in only what is missing.

## 2. Install the IP-Adapter nodes

```powershell
git clone --depth 1 https://github.com/cubiq/ComfyUI_IPAdapter_plus `
  "C:\ComfyUI-talemate\ComfyUI\custom_nodes\ComfyUI_IPAdapter_plus"
```

No dependencies to install. It needs only torch, which the embedded Python already has.

!!! tip "Deliberately not InstantID or PuLID"
    Those run InsightFace, which expects a human face and will either fail to detect one
    or normalise away exactly the traits that make a non-human character recognisable —
    violet skin, iris-less eyes. Plain IP-Adapter uses CLIP-Vision and has no face
    detector, so it handles any subject. It also avoids InsightFace's Windows build pain.

## 3. Fetch the models

```powershell
$m = "C:\ComfyUI-talemate\ComfyUI\models"
New-Item -ItemType Directory -Force -Path "$m\ipadapter","$m\clip_vision" | Out-Null

curl.exe -L --fail -o "$m\ipadapter\ip-adapter-plus_sdxl_vit-h.safetensors" `
  https://huggingface.co/h94/IP-Adapter/resolve/main/sdxl_models/ip-adapter-plus_sdxl_vit-h.safetensors

curl.exe -L --fail -o "$m\ipadapter\ip-adapter-plus-face_sdxl_vit-h.safetensors" `
  https://huggingface.co/h94/IP-Adapter/resolve/main/sdxl_models/ip-adapter-plus-face_sdxl_vit-h.safetensors

curl.exe -L --fail -o "$m\clip_vision\CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors" `
  https://huggingface.co/h94/IP-Adapter/resolve/main/models/image_encoder/model.safetensors
```

808MB, 808MB, 2.53GB. No HuggingFace login needed. The filename for the CLIP-Vision
model matters: it is renamed from the repo's `model.safetensors` so it is identifiable
in ComfyUI's dropdown.

!!! note "A download that looks stuck usually isn't"
    Windows does not refresh a directory entry's size while a file is being written, so
    `Get-ChildItem` can report `0 MB` for minutes on a healthy download. Check the
    process instead:
    `(Get-CimInstance Win32_Process -Filter "Name='curl.exe'").WriteTransferCount`

**Check:** three files present, ~4.1GB total.

## 4. Point it at the shared models

Write `C:\ComfyUI-talemate\ComfyUI\extra_model_paths.yaml`. This reuses the checkpoints
already downloaded for KoboldCpp, and the original rig's LoRAs and VAEs, without copying
or writing to either.

```yaml
comfyui_talemate:
    base_path: C:/ComfyUI-talemate/ComfyUI
    is_default: true          # keeps every new download landing locally
    checkpoints: models/checkpoints
    clip_vision: models/clip_vision
    ipadapter: models/ipadapter
    loras: models/loras
    vae: models/vae

koboldcpp_shared:
    base_path: C:/Users/<you>/AppData/Local/koboldcpp
    checkpoints: models

comfyui_2_readonly:                # no is_default here - never write to this tree
    base_path: "I:/Dropbox (Personal)/Projects/ComfyUI-2/ComfyUI-Easy-Install/ComfyUI"
    loras: models/loras
    vae: models/vae
    clip_vision: models/clip_vision
    controlnet: models/controlnet
    upscale_models: models/upscale_models
    text_encoders: models/text_encoders
    diffusion_models: |
        models/diffusion_models
        models/unet
```

## 5. Start it

```powershell
cd C:\ComfyUI-talemate
.\python_embeded\python.exe -I -W ignore::FutureWarning ComfyUI\main.py `
  --windows-standalone-build --port 8188 --disable-auto-launch
```

Port 8188 is ComfyUI's default and matches Talemate's default `api_url`. Free the GPU
first if KoboldCpp is holding it (`Get-Process koboldcpp | Stop-Process -Force`) and
unload any resident Ollama embedding model (`ollama stop <model>`).

**Check** — this is the single command that proves the whole stack, because it reports
whether the custom node loaded *and* whether every model is visible:

```powershell
$oi = Invoke-RestMethod http://127.0.0.1:8188/object_info/IPAdapterModelLoader
$oi.IPAdapterModelLoader.input.required.ipadapter_file[0]
Invoke-RestMethod http://127.0.0.1:8188/object_info/CLIPVisionLoader |
  ForEach-Object { $_.CLIPVisionLoader.input.required.clip_name[0] }
Invoke-RestMethod http://127.0.0.1:8188/object_info/CheckpointLoaderSimple |
  ForEach-Object { $_.CheckpointLoaderSimple.input.required.ckpt_name[0] }
```

Expected:

```
ip-adapter-plus-face_sdxl_vit-h.safetensors  ip-adapter-plus_sdxl_vit-h.safetensors
CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors
CyberRealisticPony_V9.0_FP16.safetensors  CyberRealistic_PonySemi_V5.safetensors  sd_xl_turbo_1.0_fp16.safetensors
```

Checkpoints appearing here is proof step 4 worked — they live in the KoboldCpp folder,
not in this install.

## 6. Configure Talemate

The workflow ships with the fork as
`templates/comfyui-workflows/sdxl-ipadapter-character.json`. Talemate binds to it by
**node title**, so any workflow works as long as it contains
`Talemate Load Checkpoint`, `Talemate Positive Prompt`, `Talemate Negative Prompt`,
`Talemate Resolution` and at least one `Talemate Reference 1`.

Visualizer settings (or `config.yaml` directly):

| Setting | Value | Why |
|---|---|---|
| Backend (text to image) | `comfyui` | one runtime for both paths |
| Backend (image editing) | `comfyui` | this is what receives references |
| ComfyUI (Text to image) → Model | `CyberRealisticPony_V9.0_FP16.safetensors` | the bundled workflow's default name may not exist locally |
| ComfyUI (Image editing) → Model | same | |
| ComfyUI (Image editing) → Workflow | `sdxl-ipadapter-character.json` | the IP-Adapter graph |
| ComfyUI (Image editing) → Prompting Type | `Keywords` | **not** Descriptive — see below |
| Prompt Generation → Perform extra revision of editing prompts | **off** | rewrites the prompt into an edit instruction like "change her pose", which throws away the scene description |
| Character References → Anchor illustrations to character cards | **on** | the feature switch |

The last two matter more than they look. The IMAGE_EDIT path was built for *editing*
("make her arms crossed"), and both defaults assume that. Here the reference is
conditioning, not an instruction: the prompt must remain the full keyword description of
the scene.

**Check:** the Visualizer agent shows a green `ComfyUI` badge for both Text-to-image and
Image editing, and the image-editing detail line reports the reference count.

## 7. Choose a backend at startup

`start-fork.bat` starts KoboldCpp by default. To use ComfyUI instead:

```bat
set USE_COMFYUI=1
start-fork.bat
```

`set SKIP_IMAGES=1` still skips image generation entirely. With `USE_COMFYUI=1` the
script starts ComfyUI on `COMFYUI_PORT` (default 8188) and leaves KoboldCpp alone, so
the two never contend for VRAM.

---

## Three workflows, and when to use which

| Workflow | Slots | Passes | Use when |
|---|---|---|---|
| `sdxl-ipadapter-character.json` | 1 | one | **Default.** Fastest, cleanest surface. Identity is strong but the reference's framing and pose bleed in |
| `sdxl-ipadapter-inpaint.json` | 1 | two | Composition matters more than surface polish. Pass 1 generates the scene with **no** reference; the person is segmented; pass 2 regenerates only inside that mask with the reference applied |
| `sdxl-ipadapter-character-multi.json` | 3 | one | You have curated 2–3 `reference`-tagged images of a character and want their identity averaged across them |

Switch by changing ComfyUI (Image editing) → Workflow.

**Match the slot count to how many references you actually have.** A workflow's slots
are all bound before dispatch — `Workflow.set_reference_images` disconnects unpopulated
reference nodes, which would leave the batch node missing an input and fail ComfyUI
validation, so Python repeats what it has to fill them. Repeating a single image is
harmless (the average of identical embeddings is that embedding, and the backend uploads
it once), but **two** images across three slots weights the first two-thirds to
one-third. That case logs `reference_slots.uneven_padding` — if you see it, either tag a
third image or switch to the single-slot workflow.

The inpaint workflow is the answer to the reference hijacking composition: because pass 1
never sees the reference, the room and the pose are the prompt's, and identity can only be
applied where the person already is. Measured trade-off: much better staging — a cluttered
working interior with a natural walking pose instead of a pin-up stance — at the cost of a
faint outline glow around the figure and a slightly plastic surface, plus ~2× generation
time.

Its own tuning knobs, measured:

| blur / expand | pass-2 `start_at_step` | weight | Result |
|---|---|---|---|
| 0 / 12 (FeatherMask) | 12 | 1.0 | Identity good, figure clearly pasted with a hard halo |
| 12 / 16 | 9 | 0.9 | Over-softened: legs dissolved into fog, figure floating |
| **6 / 8** | **14** | **0.85** | **Shipped: full figure, natural pose, faint glow only** |
| 6 / 8 | 11 | 1.0 | Subject rotated away from the prompt's pose, more glow |

`FeatherMask` is the wrong node for this and was removed: it feathers the image's *edges*
inward, not the mask's silhouette. `GrowMaskWithBlur` softens the outline, which is what
hides the seam.

## Curating the reference set

Reference slots take **more pictures of one character**, not one picture each of several.
Averaging two people's embeddings produces a third person.

Extra images are opt-in: tag an asset `reference` and it joins the set. Without tags the
character's cover image is repeated across every slot, which is deliberate — measured on
Kaira, averaging her good cover with two off-spec cards pulled the violet skin back toward
human and lost her facial markings. Blindly averaging everything stored is worse than using
one good image three times.

Untagged `CHARACTER_CARD`s are used only when a character has no cover at all.

## Tuning the reference strength

Set on the `Apply Character Reference` node in the workflow JSON. These were measured on
Kaira's card against a starship-control-room prompt, same seed:

| weight | weight_type | end_at | embeds_scaling | Result |
|---|---|---|---|---|
| 0.5 | linear | 0.6 | V only | Scene correct, **identity lost** — human skin, wrong hair |
| 0.6 | ease out | 0.7 | V only | Identity good, scene present, pose drifts to pin-up |
| 0.7 | ease out | 0.8 | V only | Single subject, correct room, suit and hair right, but **skin normalised to human** |
| **0.8** | **ease out** | **0.8** | **K+V** | **Shipped default: single subject, correct room, violet skin and markings retained** |
| 0.9 | ease out | 0.6 | V only | Single subject, scene kept, colouring only partial |
| 0.7 | linear | 1.0 | V only | Two subjects, scene replaced by the reference's framing |
| 0.85 | linear | 0.9 | V only | Identity strongest, two subjects, no scene |
| 1.0+ | style transfer | 1.0 | V only | Four subjects, no scene |

`embeds_scaling: K+V` is what finally held the skin colour. At `V only` the reference
governs *what* is attended to but influences the result more weakly, and the checkpoint's
overwhelming human-skin prior reasserted itself by the final steps. `K+V` conditions both
halves of the attention, which carried the colouring through without needing a weight high
enough to duplicate the subject.

Two further rules come out of this, and both are about **composition leaking with
identity**:

- **The weight must decay.** `ease out` with `end_at` ≤ 0.8 lets the last steps follow
  the prompt, which is what keeps the room and the pose. Constant weight to the final
  step hands composition to the reference.
- **Higher is not better.** Above ~0.8 the reference duplicates the subject — two or
  four copies of the character — because its whole composition is being reproduced.

!!! important "The reference is now the canonical character"
    Cropping the card to head-and-shoulders made things **worse**: the crop's portrait
    framing transferred, producing a close-up instead of a scene. IP-Adapter copies the
    reference's framing along with its identity, so the reference should be a
    **full-body, plainly-lit, neutrally-posed** image of the character wearing their
    usual clothing.

    A glamour-style card teaches the model glamour poses. Kaira's current cover is also
    pale lavender where her prose says "deep violet", and the generated images faithfully
    reproduce the card, not the prose — so the card is the thing to fix. Whatever you set
    as the character's cover image is now the single source of truth for their appearance.

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'comfy.ldm.models'` / torchvision circular import**
: The copy excluded directories named `models`. See the warning in step 1.

**`ERROR : Invalid Parameter #3 : "E:/"`**
: robocopy was run from Git Bash. Use PowerShell.

**Visualizer image-editing badge is red, "no reference nodes found in workflow"**
: The selected workflow has no `Talemate Reference 1` node. Check the title spelling —
  binding is by exact title prefix, `Talemate Reference ` plus a 1-based index.

**Images generate but ignore the reference entirely**
: The request never routed to IMAGE_EDIT. Either Character References is off, or the
  scene illustration's prompt does not name the character — selection reads the assembled
  prompt for names, the same evidence anchor pruning uses.

**Two or four copies of the character**
: Reference weight too high, or weight held constant to the last step. See the table.

**A close-up portrait instead of a scene**
: The reference is a portrait. Use a full-body reference.

**Out of VRAM, or ComfyUI is very slow**
: KoboldCpp or a resident Ollama model still holds the GPU. `nvidia-smi` to confirm,
  then stop KoboldCpp and `ollama stop <model>`.

**Text generation starts failing after images have been generated**
: ComfyUI caches the checkpoint in VRAM after a run — measured at ~10.9GB of 16GB with
  SDXL + IP-Adapter resident — which can leave too little for Ollama to load a text model.
  Free it without restarting:

  ```powershell
  Invoke-RestMethod http://127.0.0.1:8188/free -Method Post `
    -Body '{"unload_models":true,"free_memory":true}' -ContentType 'application/json'
  ```

  Or start ComfyUI with `--reserve-vram 5` to keep headroom permanently. Talemate does not
  call `/free` itself.
