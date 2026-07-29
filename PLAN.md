# PLAN — phased implementation

Companion to `ARCHITECTURE.md`. Difficulty is engineering effort; risk is chance
of breaking or of churn against upstream. Anything I think is a bad idea is
flagged **⚠️ PUSHBACK**.

---

## Summary of what recon changed

| Brief assumption | Reality |
|---|---|
| Ollama `/v1/images/generations` may work | **Blocked on Windows** — MLX runner, Apple Silicon only. Tested. |
| Existing OpenAI visual backend might just be repointed | **No** — `openai` has no `base_url`; `openai_compatible` is analysis-only |
| KoboldCpp is a fallback to "evaluate, don't implement" | **Already fully implemented upstream, zero code** — and it's the only working Windows path |
| Phase 1 might be "a config change plus docs" | It's a config change plus docs — but for **KoboldCpp**, not Ollama |
| Video agent must be made async/non-blocking | Already free — `set_background_processing` / `run_tracked_task` |
| New agents mean core diffs | No — `agents/custom/` auto-imports. Zero diff. |

**⚠️ PUSHBACK — Phase 1 should be reordered.** The brief spends Phase 1 on the
Ollama image path and defers KoboldCpp to a write-up. Recon inverts that: the
Ollama path cannot work on your machine, and the KoboldCpp path already works
with no code at all. I'd make KoboldCpp the Phase 1 deliverable (it gets you
inline scene images *today*), and demote the Ollama adapter to a
speculative item gated on Ollama shipping a non-MLX runner.

I have not acted on this — reordering Phase 1 is your call. Everything below is
written so either ordering works.

---

## Phase 1 — Images without ComfyUI

Goal unchanged: inline scene images, no ComfyUI.

### 1A. KoboldCpp path — **recommended first**
**Difficulty: trivial (docs only). Risk: low.**

No code. Steps are: run KoboldCpp with a text GGUF **and** an SD model
(`--sdmodel`), add it as a Talemate client, leave the Visualizer's
`automatic_setup` at its default `True`. The client's `visual_automatic1111_setup`
(`client/koboldcpp.py:516-564`) probes `/sdapi/v1/sd-models` and self-configures
the `automatic1111` backend at the KoboldCpp URL.

- Deliverable: `docs/` page + manual test steps.
- Caveat: this makes KoboldCpp your text backend too, so it displaces Ollama for
  narration unless you run both and point agents at different clients. Worth
  deciding explicitly — **question for you** (see end).
- Needs you to test: I cannot verify anything requiring a loaded model.

### 1B. `base_url` on the `openai` visual backend
**Difficulty: low (~40 lines). Risk: low-medium (touches an upstream file).**

Add an optional `base_url` config to `openai_shared_config()`
(`backends/openai_image.py:242-274`) and thread it into the four `AsyncOpenAI(...)`
constructions (`:93, 109, 131, 189`), defaulting to unset = current behaviour.

- **This does *not* unlock Ollama on Windows** (MLX gate). Its value is other
  OpenAI-shaped image servers.
- ⚠️ Modifies an upstream file in 5 places → merge risk. Mitigation: keep it to
  `base_url = self.base_url or None` passed as a kwarg, and record in `FORK.md`.
- ⚠️ **Mild pushback:** if 1A gives you working images, this is speculative
  scaffolding. I'd defer it until there's a concrete server to point it at.

### 1C. Dedicated `ollama` visual backend adapter
**Difficulty: medium (~250 lines). Risk: medium. Recommend: DEFER.**

Follows the existing pattern exactly: new file `backends/ollama_image.py` with a
`@backends.register`ed `Backend` (`image_create=True`) and an `OllamaImageMixin`
supplying `ollama_backend`, `ollama_prepare_generation`, `ollama_emit_status`,
`add_actions`.

- Unavoidable core diff: **3 lines in `visual/agent.py`** — the import (~`:34`),
  the mixin in the class bases (`:54-66`), and the `add_actions` call (`:205-213`).
  There is no plugin discovery for visual backends the way there is for agents.
  That's the minimum possible hook; I'd accept it.
- ⚠️ **PUSHBACK: do not build this yet.** It cannot be tested on Windows. Writing
  an adapter against an endpoint we can't exercise means shipping untested code
  against an experimental API whose response shape may change. Revisit when
  Ollama ships a CUDA/CPU image runner.

### 1D. KoboldCpp write-up
**Difficulty: trivial.** The brief asks for evaluation only. Largely covered in
`ARCHITECTURE.md §6.3`; I'd fold it into the 1A doc rather than write a separate
speculative document, since it turned out to be the real path.

---

## Phase 2 — Your feature list

Still `TBD` in the brief. Notes that will matter when you fill it in:

- **Agent settings UI is free** (`Agent.config_options()` → generic Vue
  renderer). Any feature expressible as agent config costs no frontend work.
- **Bespoke UI is not free.** Anything with its own panel means real Vue work.
- **Don't touch `prompts/templates/conversation/dialogue.jinja2`** — highest-churn
  file in the repo (§5.4). Use `DynamicInstruction` (`base.py:230-240`) or
  template overrides in `templates/prompts/<agent>/` instead.
- **Dice already exist**: `agents/director/modules/roll-dice.json`. If dice
  mechanics were on your list, check that first — it may be cut scope.
- **Scene branching/saving already exists** (`save.py`, `export.py`,
  changelog machinery). Likewise check before building.
- Per-scene config overrides are a built-in concept (`resolve_config`,
  `base.py:907-921`) — use them rather than inventing scene state.

I'll estimate properly once the list exists.

---

## Phase 3 — Video agent

Structure is clear and the brief's instinct to mirror the visual agent is right.
Three real problems, one of which the brief doesn't anticipate.

### 3A. The agent skeleton
**Difficulty: low-medium. Risk: low.**

`src/talemate/agents/custom/video/__init__.py` — `@register()`, `agent_type =
"video"`, `requires_llm_client = True` (it needs an LLM to write clip prompts),
`essential = False`, and `has_toggle = True` with `is_enabled = False` default to
satisfy "opt-in, off by default". **Zero upstream files modified** thanks to
`agents/custom/` auto-import (§2.2).

### 3B. ComfyUI video backend
**Difficulty: medium. Risk: low-medium.**

Near-copy of `backends/comfyui.py` with:
- its own workflow dir — I'd use `templates/comfyui-workflows/video/` to stay
  inside the convention the brief cites, or a sibling `comfyui-video-workflows/`.
- output parsing for `gifs`/`videos` keys instead of `images` in the
  `/history/{id}` outputs (`comfyui.py:486-496`).
- the same node-title contract (`"Talemate Positive Prompt"`, etc., §4.7) so your
  exported API JSON works the same way.

Non-blocking is free: reuse `set_background_processing` / `run_tracked_task`
(§2.6). "Assume the clip arrives late and attaches retroactively" is already how
images work — `GenerationRequest.callback` plus the `on_done` pattern
(`generation.py:148-165`).

**Needs ComfyUI running, so it's you-tests-it.** Also: this contradicts the
brief's "defer ComfyUI as long as possible" — Phase 3 *is* the ComfyUI phase, so
that deferral ends here. Fine, just naming it.

### 3C. ⚠️ Asset delivery — the real problem
**Difficulty: medium-high. Risk: HIGH. Needs a decision before 3B ships.**

Assets reach the frontend **only** as base64 over the websocket
(`server/scene_assets_batching.py:28-47`, §4.6). There is no HTTP asset route.

A 10 MB clip becomes a ~13 MB JSON websocket frame, sent through a 100 ms
debounce batcher, with no streaming, no range requests, and no seeking. For
images (tens of KB) this is fine. For video it is not — expect UI stalls and
memory spikes, and browsers cannot seek a `data:` URI video efficiently.

Options:

1. **Add a static/range HTTP route for assets** (e.g. `GET /assets/{scene}/{id}`)
   and have video use `<video src>`. Correct fix, reusable for images later.
   Cost: a new route in `src/talemate/server/` — a genuine core diff, though an
   *additive* one (new file + one router include), which is the good kind.
2. **Base64 anyway, cap clip length/resolution** (2-3 s, 512px, low bitrate).
   Zero architecture change, ships fastest, degrades badly if you later want
   longer clips.
3. Write clips outside the asset system and serve from a separate mount. Avoids
   touching `scene_assets.py` but forks the asset model — I'd avoid.

**Recommendation: option 1**, and if you want it cheap, do option 2 first as a
spike and upgrade. `Asset.media_type` is already a stored per-asset field
(§4.5), so the storage layer needs no change either way — this is purely about
transport.

### 3D. ⚠️ Frontend — under-scoped in the brief
**Difficulty: medium-high. Risk: medium.**

The brief treats the video agent as backend work. It isn't, entirely. The
Visualizer's UI is ~16 bespoke Vue components (`VisualLibrary.vue`,
`VisualQueue.vue`, `VisualImageView.vue`, `VisualLibraryPendingQueue.vue`, …).
Agent *settings* come free; **playback, a clip library, and "attach to this scene
beat" affordances do not.**

Minimum viable: render an inline `<video>` on a message that has a video asset,
plus a pending indicator. That is still new Vue work. Worth scoping explicitly
before starting, and worth considering whether clips should just appear in the
existing visual library with a video thumbnail rather than getting their own UI.

### 3E. Node registration
**Difficulty: low. Risk: low.**

Python nodes registered via `@register("agents/video/...")` in the custom agent's
`nodes.py`, imported from its `__init__.py`. Declarative modules go in the agent
package's own `modules/*.json` — picked up automatically because
`src/talemate/agents` is on `SEARCH_PATHS` (§3.2). Remember to extend
`TYPE_CHOICES` for any new socket types (pattern at `visual/nodes.py:50-58`).

---

## Cross-cutting risk register

| Risk | Severity | Note |
|---|---|---|
| `dialogue.jinja2` volatile-context ordering | **High** | Reorders on a caching setting; upstream churns it. Never edit. |
| No HTTP asset route (video) | **High** | Blocks Phase 3 quality. Decide 3C early. |
| `visual/agent.py` mixin stack | Medium | Any new visual backend = 3-line diff. Unavoidable, acceptable. |
| Node 25 vs project's Node 22 | Medium | Works now; first suspect for weird frontend behaviour. |
| Embedding change = full re-index | Low-Medium | Don't change embedding presets casually (§5.1). |
| `exclude-newer = "1 week"` in pyproject | Low | Dependency resolution is time-pinned; surprising on fresh installs. |
| Dropbox syncing `.venv`/`node_modules` | Medium | See below. |

**Non-code recommendation:** this repo lives in `I:\Dropbox (Personal)\`. `.venv`
(445 packages incl. torch) plus `node_modules` is on the order of 10⁵ files that
Dropbox will try to sync continuously — that's slow builds, CPU burn, and
potential file-lock flakiness during installs. I'd either add Dropbox ignore
markers to `.venv/` and `talemate_frontend/node_modules/`, or move the working
copy outside Dropbox and rely on the GitHub fork for backup. Your call; I haven't
changed anything.

---

## Suggested order

1. **1A** KoboldCpp docs + you test → working inline images, no code. *(now)*
2. **Phase 2** once you've filled in the feature list. Cheapest wins first.
3. **3C decision** (asset transport) — do this *before* 3B so the backend isn't
   built against the wrong assumption.
4. **3A → 3B → 3E** video backend.
5. **3D** frontend, scoped separately.
6. **1B/1C** only if a concrete need appears.

---

## Questions before Phase 1

1. **Reorder Phase 1 to lead with KoboldCpp?** (my recommendation — see top)
2. **Do you have KoboldCpp and an SD/GGUF model, or should I write the docs
   against a setup you'll build?** I can't test either way, but it changes how
   prescriptive the doc should be.
3. **If KoboldCpp becomes the image path, does it also become the text path?**
   Or run Ollama for narration + KoboldCpp for images, with agents pointed at
   different clients? Affects the doc and your VRAM budget.
4. **Phase 3 asset transport: option 1 (HTTP route) or option 2 (base64 + short
   clips)?** Only needs answering before Phase 3.

Stopping here per the brief — not starting Phase 1.
