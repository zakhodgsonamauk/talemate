# Scene images without ComfyUI (KoboldCpp + SDXL Turbo)

Fork-specific setup guide. Gets inline scene images working with **no ComfyUI**,
while Ollama keeps serving text.

This is a **configuration-only** path — it required no code changes to Talemate.

---

## Why this and not Ollama

Ollama exposes an OpenAI-compatible `/v1/images/generations` endpoint, and the
original plan was to point Talemate at it. **That does not work on Windows.**

The route exists, but Ollama dispatches image generation to an **MLX runner**, and
MLX is Apple-Silicon only. Verified against Ollama 0.32.5 on Windows 11 with a
FLUX model (`x/flux2-klein`) pulled and available:

```console
$ curl -X POST http://localhost:11434/v1/images/generations \
    -d '{"model":"x/flux2-klein:latest","prompt":"a tavern","size":"512x512"}'
500
{"error":{"message":"mlx runner failed: Error: failed to initialize MLX:
  failed to load MLX dynamic library (searched: ...)"}}
```

This is a platform gate, not a model or config problem. No Talemate-side adapter
can work around it. Re-test after any Ollama release that ships a CUDA/CPU image
runner.

Two related dead ends, for the record:

- Talemate's `openai_compatible` visual backend **has** a configurable base URL but
  is **analysis-only** (`image_create = False`) — it captions images, it does not
  generate them.
- Talemate's `openai` image backend **can** generate but hardcodes its client with
  no base URL, so it cannot be repointed without a code change.

KoboldCpp is the working path, and Talemate already auto-detects it.

---

## What you get

- Image generation on your GPU via KoboldCpp's AUTOMATIC1111-compatible API.
- Talemate **auto-configures itself** the moment you add the client.
- Ollama stays the text backend — no VRAM contention, no model swapping.
- ~5–15 s per 1216×832 illustration on an RTX 4080.

---

## Prerequisites

- An NVIDIA GPU. Budget ~7 GB VRAM for SDXL, plus whatever your text model needs.
- Ollama running with a text model (this guide assumes it is your narrator).
- Talemate installed and running (see the fork's `ARCHITECTURE.md` §7 for the
  non-`install.bat` startup commands).

!!! tip "Already set up? Just run `start-fork.bat`"
    Once you've done the one-time setup below, **`start-fork.bat`** in the repo
    root starts Ollama, KoboldCpp, the backend and the frontend, waits for each,
    and opens Chrome. It skips anything already listening, so re-running is safe.

    Useful switches: `set SKIP_IMAGES=1` (text only), `set NO_BROWSER=1`, and
    `KCPP_MODEL` / `KCPP_PORT` / `TALEMATE_BACKEND_PORT` /
    `TALEMATE_FRONTEND_PORT` to override the defaults.

    Do **not** use upstream's `start.bat` / `start-backend.bat` — they require the
    embedded Python that `install.bat` provisions, and will trigger a full
    reinstall on this checkout.

---

## 1. Get KoboldCpp

Download the CUDA build (`koboldcpp.exe`, ~640 MB) from the
[releases page](https://github.com/LostRuins/koboldcpp/releases). Anywhere outside
your Dropbox/OneDrive folder is a good idea.

```powershell
$dir = "$env:LOCALAPPDATA\koboldcpp"
New-Item -ItemType Directory -Force -Path "$dir\models" | Out-Null
curl.exe -L -o "$dir\koboldcpp.exe" `
  https://github.com/LostRuins/koboldcpp/releases/download/v1.117.1/koboldcpp.exe
```

!!! note "Which build"
    `koboldcpp.exe` bundles CUDA. `koboldcpp-nocuda.exe` (113 MB) is CPU-only and
    far too slow for inline images. `koboldcpp-oldpc.exe` targets pre-AVX2 CPUs.

## 2. Get an image model

**SDXL Turbo** is the recommended default: it generates usable images in ~6 steps
instead of ~40, which is what makes inline generation tolerable mid-story.

```powershell
curl.exe -L -o "$env:LOCALAPPDATA\koboldcpp\models\sd_xl_turbo_1.0_fp16.safetensors" `
  https://huggingface.co/stabilityai/sdxl-turbo/resolve/main/sd_xl_turbo_1.0_fp16.safetensors
```

6.6 GB, no HuggingFace login required. Any SD 1.5 / SDXL `.safetensors` or GGUF
checkpoint works — but if you use a non-Turbo model you must change the sampler
settings in step 5 (see the table).

## 3. Start KoboldCpp in image-only mode

You do **not** need a text model. KoboldCpp accepts `--sdmodel` on its own and
starts with only its image module active, which leaves Ollama untouched.

```powershell
$dir = "$env:LOCALAPPDATA\koboldcpp"
& "$dir\koboldcpp.exe" `
  --sdmodel "$dir\models\sd_xl_turbo_1.0_fp16.safetensors" `
  --usecuda --port 5001 --skiplauncher
```

Expect this in the output:

```
Load Image Model OK: True
Active Modules: ImageGeneration
Inactive Modules: TextGeneration ...
Enabled APIs: KoboldCppApi A1111ForgeApi ComfyUiApi
Starting Kobold API on port 5001 at http://localhost:5001/api/
```

Sanity-check the endpoint Talemate probes:

```powershell
(Invoke-WebRequest http://localhost:5001/sdapi/v1/sd-models).Content
# [{"title": "sd_xl_turbo_1.0_fp16", "model_name": "sd_xl_turbo_1.0_fp16", ...}]
```

A non-empty `model_name` here is the trigger for Talemate's auto-setup. If this
returns an empty list, auto-setup will silently do nothing.

!!! tip "Port choice"
    5001 is KoboldCpp's default and matches Talemate's default client URL, so
    keeping it means zero typing later. Do **not** use 11434 — that is Ollama.

## 4. Add the client in Talemate

In the Talemate UI: **Add Client → KoboldCpp**. The default API URL is already
`http://localhost:5001`. Save.

Talemate now configures the Visualizer for you. The backend log shows:

```
KoboldCpp AUTOMATIC1111 setup      sd_model=sd_xl_turbo_1.0_fp16
reinitializing automatic1111 backend  api_url=http://localhost:5001
```

and the **Visualizer** agent gains a green `AUTOMATIC1111` badge.

!!! info "How the auto-setup works"
    The Visualizer's `automatic_setup` option (**on by default**) makes it look for
    a `visual_<backend>_setup` method on the active client each status tick.
    KoboldCpp implements `visual_automatic1111_setup`: it probes
    `/sdapi/v1/sd-models` and, if a model is loaded, sets the backend to
    `automatic1111` and points it at the client's own URL. It is idempotent and a
    no-op when no image model is present.

### Keep Ollama as the text backend

Adding KoboldCpp will reassign **every** text agent to it, because Talemate hands
agents the first enabled client. KoboldCpp has no text model loaded, so leave it
that way and text generation fails.

Fix it one of two ways:

- **Simplest** — disable the KoboldCpp *client* (the power icon on its card). All
  agents fall back to Ollama, and the image backend keeps working: the
  `automatic1111` backend just posts to its configured URL and does not care
  whether the client is enabled.
- **Explicit** — leave both enabled and set each agent's **Client** to Ollama in
  its settings.

!!! warning "Trade-off of disabling the client"
    With the KoboldCpp client disabled, `automatic_setup` no longer re-runs. The
    saved backend config persists, but if you later change the Visualizer backend
    you will need to re-enable the client to let it re-detect.

## 5. Tune the sampler — **required for Turbo**

Talemate's AUTOMATIC1111 defaults are written for standard SD models and will
produce **badly artifacted, oversaturated garbage** with SDXL Turbo.

Visualizer → **AUTOMATIC1111 (Text to image)**:

| Setting | Default | Set to (SDXL Turbo) | Why |
|---|---|---|---|
| **Sampling Method** | `DPM++ 2M` | **`Euler a`** | The big one. DPM++ 2M at low steps destroys the image. |
| **Steps** | 40 | **6** | Turbo is distilled for 1–8 steps. |
| **CFG Scale** | 7.0 | **1.0** | Turbo is trained for cfg ≈ 1. Higher values blow out contrast. |
| Prompting Type | `Keywords` | `Keywords` | Correct for SDXL. Use `Descriptive` for Flux/Qwen. |
| Resolutions | 1024², 832×1216, 1216×832 | unchanged | Already SDXL-correct. |

!!! danger "If you skip the sampler change"
    Steps and CFG alone are not enough. With `DPM++ 2M`, steps 6 and cfg 1.0 you
    still get a high-contrast noise mess that looks like a broken VAE. It is the
    sampler.

For a **non-Turbo** SDXL or SD 1.5 checkpoint, use the upstream defaults instead
(steps 30–40, cfg 7, `DPM++ 2M`) and expect ~30–60 s per image.

---

## Manual test steps

Please run these — I cannot verify anything that needs a GPU or a loaded model.

1. **KoboldCpp is serving images.** With KoboldCpp running, browse to
   <http://localhost:5001/sdui/> and generate anything. If this fails, the problem
   is KoboldCpp, not Talemate.

2. **Direct API check** (this is exactly what Talemate sends):

   ```powershell
   $body = @{ prompt='stone tavern interior, warm firelight'; steps=6
              width=1216; height=832; cfg_scale=1.0; sampler_name='Euler a' } | ConvertTo-Json
   $r = Invoke-WebRequest http://localhost:5001/sdapi/v1/txt2img -Method POST `
        -Body $body -ContentType 'application/json' -TimeoutSec 300
   $j = $r.Content | ConvertFrom-Json
   [IO.File]::WriteAllBytes("$env:TEMP\test.png", [Convert]::FromBase64String($j.images[0]))
   Start-Process "$env:TEMP\test.png"
   ```

   Expect a coherent image in roughly 10–15 s. **If this looks like noise, your
   sampler settings are wrong** — go back to step 5.

3. **Auto-setup fires.** Add the KoboldCpp client in Talemate. Confirm the
   Visualizer agent shows a green `AUTOMATIC1111` badge without you selecting a
   backend manually.

4. **Text still works.** Load a scene. Confirm narration/dialogue generates — that
   proves agents are on Ollama, not on the text-less KoboldCpp.

5. **End-to-end image.** In a loaded scene, use the scene toolbar's image button →
   **Visualize Moment (Illustration)**. Expect:
   - the Visualizer badge to go busy, and the story loop to **stay responsive**
     (generation is backgrounded, it never blocks the scene);
   - a new entry in the **Visual Library** review queue within ~30 s;
   - metadata showing `automatic1111`, `SCENE_ILLUSTRATION`, `1216 × 832`, and a
     keyword prompt built from your actual scene context.
6. **Judge the image.** It should match the scene, not just be "an image". If the
   prompt looks right but the picture is mush, that is a model/sampler issue, not
   a Talemate issue.

7. **Persistence.** Restart the backend. Confirm the Visualizer still shows
   `AUTOMATIC1111` and your steps/cfg/sampler values survived.

---

## Troubleshooting

**Memory agent: "Failed to set up the database: Could not load libtorchcodec"**

Not image-related, but it blocks scene loading, so you may hit it first. Two
causes, and on Windows you need both fixes:

1. FFmpeg's shared libraries are missing. Run the repo's `install-ffmpeg.bat`
   (skipped if you did not use `install.bat`). It drops FFmpeg 8 DLLs into
   `.venv\Scripts`.
2. Python 3.8+ does not search the executable's directory when `ctypes` loads a
   DLL, so torchcodec cannot find either FFmpeg **or** torch's own libraries. Add
   both directories at interpreter startup with a `.pth` file in site-packages:

   ```powershell
   $sp = (Resolve-Path .venv/Lib/site-packages).Path
   $tl = Join-Path $sp 'torch\lib'; $sc = (Resolve-Path .venv/Scripts).Path
   $line = 'import os; os.name == "nt" and [os.add_dll_directory(d) ' +
           'for d in [r"' + $tl + '", r"' + $sc + '"] if os.path.isdir(d)]'
   Set-Content (Join-Path $sp 'zzz_torchcodec_dll_fix.pth') $line -Encoding ascii
   ```

   Verify with
   `.venv\Scripts\python.exe -c "from sentence_transformers import SentenceTransformer; print('ok')"`.

   This is a **venv-local** workaround, not a repo change — it does not survive
   deleting `.venv`. Root cause is dependency drift: `pyproject.toml` sets
   `exclude-newer = "1 week"` *relative to install time*, so a fresh install
   resolves newer torch/torchcodec than upstream tested against.

**Images are noise / oversaturated / look like a broken VAE**
: Sampler. Set **Euler a** (step 5). This is by far the most common failure.

**Images are solid black**
: fp16 VAE overflow. Restart KoboldCpp with `--sdvaeauto` to use the built-in
  TAESD VAE, which is fast and sidesteps broken VAEs.

**Visualizer says "No backend configured" after adding the client**
: `/sdapi/v1/sd-models` returned nothing — KoboldCpp has no image model loaded.
  Check its startup output for `Load Image Model OK: True`. Also confirm
  **Automatic Setup** is still checked in Visualizer → General.

**Every agent switched to KoboldCpp and text generation broke**
: Expected. See *Keep Ollama as the text backend* above.

**"Could not determine LLM prompt template for this model"**
: Talemate's auto-detect missed your Ollama model. Open the client's settings and
  pick a template manually — Qwen3 models want **ChatML**. Leaving it on
  `default.jinja2` (an Alpaca-style fallback) measurably degrades output.

**All your Ollama models suddenly disappeared**
: `OLLAMA_MODELS` isn't set for whatever process started Ollama, so it fell back to
  `%USERPROFILE%\.ollama\models` instead of your real library. Nothing is deleted —
  Ollama just isn't looking in the right place. Persist it and restart Ollama:

  ```powershell
  setx OLLAMA_MODELS "<path to your models dir>"
  ```

  `start-fork.bat` deliberately **refuses** to start Ollama when `OLLAMA_MODELS` is
  unset, rather than starting it against a near-empty directory. Machine-specific
  paths go in the untracked `start-fork.local.bat`.

**"Another Talemate frontend is already connected. Only one connection is allowed."**
: The backend accepts a **single** frontend websocket. Close the other tab/window.
  Worth knowing when testing — a second browser will be refused, and that refusal
  is itself proof the backend is healthy.

**Generation is slower than expected**
: KoboldCpp uses `stable-diffusion.cpp`, which is slower than diffusers, and it
  puts CLIP on CPU by default (`Backend assignment: "CLIP=CPU"`). Try
  `--sdconvdirect full`, drop to 4 steps, or generate at a smaller resolution.

**Out of VRAM**
: SDXL holds ~7 GB while resident. On 16 GB, keep your Ollama text model around
  5 GB (e.g. an 8B q4). Or add `--sdquant 2` to load the image model at q4.

---

## Reference: measured behaviour

Recorded on an RTX 4080 (16 GB), Windows 11, KoboldCpp 1.117.1, SDXL Turbo fp16.

| Thing | Value |
|---|---|
| Direct `txt2img`, 1216×832, 6 steps, Euler a | ~14 s |
| In-app *Visualize Moment*, incl. LLM prompt generation | ~5–30 s |
| KoboldCpp VRAM, image model resident | ~7 GB |
| Ollama `qwen3:8b-q4_K_M` alongside | ~5 GB |

Blocking behaviour: none observed. Generation runs through the Visualizer's
background-task path, the agent reports `busy_bg`, and the scene stays
interactive — which is also the mechanism the planned video agent will reuse.
