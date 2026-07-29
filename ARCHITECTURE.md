# ARCHITECTURE — Talemate fork recon

Map of the subsystems we care about. Written against our fork point; every claim
below was read out of the code, not inferred from docs.

**Fork point:** `c12a82930e913816fdac21aedada1962ac45c3d7` — tag `0.38.0`, committed
2026-07-02. `upstream/main` was identical to this at clone time.
**Our branch:** `feature/tell-me-a-story` off `main`.

Line references are `path:line` against that SHA.

---

## 0. Headline findings

Three things that change the plan and should be read before anything else.

1. **Ollama's `/v1/images/generations` cannot work on this machine.** The route
   exists on the installed Ollama 0.32.5, but its runner is MLX — Apple Silicon
   only. Verified empirically (§6.2). Phase 1 as written in the brief is blocked
   on Windows, and no amount of Talemate-side code fixes it.
2. **The KoboldCpp path is already fully implemented upstream** and needs zero
   code from us (§6.3). The brief lists it as a fallback to "evaluate, don't
   implement". It is in fact the only working no-ComfyUI image path on Windows.
3. **The existing `openai_compatible` visual backend cannot be repointed for
   image generation** — it is analysis-only (`image_create = False`). And the
   `openai` *image* backend has no configurable base URL. So the brief's "is
   this just a config change?" question resolves to **no** (§4.3).

---

## 1. Repo layout

| Path | What |
|---|---|
| `src/talemate/` | Python backend (FastAPI + websockets) |
| `src/talemate/agents/` | The agent implementations — our main work area |
| `src/talemate/client/` | LLM client adapters (Ollama, KoboldCpp, OpenAI, …) |
| `src/talemate/game/engine/nodes/` | Node-graph engine + registry |
| `src/talemate/prompts/templates/` | Jinja2 prompt templates (**merge hotspot**) |
| `src/talemate/server/` | Websocket router + per-domain plugins |
| `talemate_frontend/` | Vue 3 + Vuetify + Vite frontend |
| `templates/comfyui-workflows/` | ComfyUI API-format workflow JSON |
| `templates/modules/` | **Third-party node modules — zero-diff extension point** |
| `scenes/` | Scene saves + their assets |

Backend is `uv`-managed (`pyproject.toml`), Python pinned to 3.11 by
`.python-version`. Frontend uses **pnpm 11.4.0** pinned via `packageManager` in
`talemate_frontend/package.json:5`.

---

## 2. Agents: definition, registration, config, invocation

### 2.1 Registration

`src/talemate/agents/registry.py` is the whole mechanism — 27 lines:

- `AGENT_CLASSES: dict` (`registry.py:3`)
- `@register(condition=None)` decorator keys the class by its `agent_type`
  attribute (`registry.py:6-19`). An optional `condition` callable can skip
  registration.

Built-in agents are then explicitly imported in `agents/__init__.py:1-12`. Adding
a line there would be a core diff — **but we don't need to**, see below.

### 2.2 The zero-diff extension point

`src/talemate/agents/custom/__init__.py:13-34` walks its own directory and
`importlib.import_module`s **every subdirectory** as a package, logging
`"activating custom agent"`. Drop a package in `agents/custom/<name>/` with a
`@register()`ed class in its `__init__.py` and it is live — **no upstream file
modified**.

`agents/custom/third_party_agents_go_here.txt` confirms this is the sanctioned
path and points at the worked example:
`docs/dev/agents/example/test/__init__.py` — a complete agent in ~50 lines.

### 2.3 Minimum viable agent

From that example, the required surface is:

```python
@register()
class TestAgent(Agent):
    agent_type = "test"        # registry key + config key + scene-state key
    verbose_name = "Test"      # UI label

    def __init__(self, client):
        self.client = client
        self.is_enabled = True
        self.actions = {"test": AgentAction(enabled=True, label="Test", description="Test")}

    @property
    def enabled(self): return self.is_enabled
    @property
    def has_toggle(self): return True
    @property
    def experimental(self): return True

    def connect(self, scene):
        super().connect(scene)
        talemate.emit.async_signals.get("game_loop").connect(self.on_game_loop)
```

Class-level knobs on `Agent` worth knowing (`agents/base.py:389-405`):
`requires_llm_client = True` (set False for a pure-IO agent), `essential = True`,
`websocket_handler = None`.

### 2.4 Config schema and the free UI

Agent settings are declared, not hand-built:

- `AgentActionConfig` (`base.py:65-139`) — a typed field. `type` is one of
  `autocomplete, blob, bool, flags, number, text, vector2, weights, wstemplate,
  password, unified_api_key` (`base.py:66-78`). Supports `choices`,
  `condition` (conditional display), `note_on_value`, `save_on_change`,
  `scene_overridable`.
- `AgentAction` (`base.py:142-180`) — a group of configs; `container=True` renders
  as a collapsible section, `icon`, `subtitle`, `condition`.
- `AgentActionConditional` (`base.py:56-58`) — show a field only when another
  attribute has a given value. This is how backends hide each other's settings,
  e.g. `attribute="_config.config.backend", value="comfyui"`.

`Agent.config_options()` (`base.py:416-435`) serialises all of this and it is
emitted as the `agent_status` payload (`base.py:1071-1081`). The frontend renders
it **generically** via `AgentSettingField.vue` / `AgentGlobalSettings.vue` /
`AgentModal.vue`. **Consequence: a new agent gets its entire settings UI for
free.** No Vue work is needed for configuration.

There is also a "dynamic registry" facility (`base.py:183-197, 696-882`) for
user-managed lists of named sub-configs (used by TTS for its OpenAI-compatible
backends). Relevant if we ever want N named video backends.

### 2.5 Instantiation and lifecycle

`src/talemate/instance.py` holds the live singletons:

- `AGENTS`/`CLIENTS` dicts (`instance.py:19-20`), `get_agent(typ)` (`:23`).
- `instantiate_agents()` (`:159-195`) iterates `AGENT_CLASSES`, pulls
  `config.agents[typ]`, resolves the named `client` to a live client instance,
  then `await agent.apply_config(actions=..., enabled=...)`.
- `ensure_agent_llm_client()` (`:226-259`) assigns a client to every agent with
  `requires_llm_client`, falling back to the first enabled client.
- `agent_ready_checks()` (`:140-149`) calls `ready_check()` on enabled agents and
  `setup_check()` on **all** agents every status tick — `setup_check` must be
  safe to call while disabled.

Persistence: `Agent.save_config()` (`base.py:1008-1034`) writes back into
`config.agents[agent_type]`. Note it **skips `unified_api_key` fields** (`:1029`)
— those live in the global config/keyring, which is how secrets stay out of the
agent config.

### 2.6 Invocation

Three ways an agent method gets called:

1. **`@set_processing`** (`base.py:317-386`) — the standard decorator. Wraps the
   call in `ClientContext()` + `ActiveAgent()`, emits busy/idle status, and
   applies per-action client overrides. Almost every public agent method has it.
2. **Websocket** — `Agent.websocket_handler` points at a `Plugin` subclass with a
   `router` name; methods named `handle_<action>` are dispatched. See
   `agents/visual/websocket_handler.py:47-162`.
3. **Node graph** — `AgentNode` subclasses in each agent's `nodes.py`.
   `Agent.init_nodes()` (`base.py:455-503`) also auto-wires nodes whose base type
   is `agents/AgentWebsocketHandler` into the websocket handler.

**Background work.** Two reusable primitives, both already async and
non-blocking — Phase 3 does not need to invent anything here:

- `set_background_processing(task, error_handler)` (`base.py:1160-1172`) — status
  reports `busy_bg` instead of the blocking-looking `busy`.
- `run_tracked_task(key, coro_factory, background=True, cancel_in_flight=False)`
  (`base.py:1196-1247`) — single-flight per `key`, self-cleaning task table.

Scene-scoped config overrides: `resolve_config(action, field)` /
`resolve_enabled(action)` (`base.py:907-921`) consult the scene overlay before the
global value. **Use these, not `self.actions[...].config[...].value`**, for
anything a scene should be able to override.

---

## 3. Node editor

### 3.1 Registration

`src/talemate/game/engine/nodes/registry.py`:

- `NODES: dict` (`:35`), `@register(name, as_base_type=False, container=None)`
  (`:113-128`). Name is a path like `agents/visual/GenerateImage`.
- `get_node(name)` (`:70-84`) checks scene-local `_NODE_DEFINITIONS` first, then
  `NODES` — scene nodes shadow global ones.
- `export_node_definitions()` (`:165-219`) serialises every node's property fields
  for the frontend editor.
- `validate_registry_path()` (`:131-162`) — a path must have ≥2 parts and must not
  be a prefix of an existing path.

### 3.2 Two kinds of node

**Python nodes** — subclass `Node`/`AgentNode`, declare a `Fields` inner class of
`PropertyField`s, implement `setup()` (declare sockets) and `async run(state)`.

**JSON module nodes** — declarative graphs loaded from disk.
`import_talemate_node_definitions()` (`registry.py:231-266`) `rglob`s `*.json`
under `SEARCH_PATHS` (`nodes/__init__.py:10-19`):

```python
SEARCH_PATHS = [
    os.path.join(TALEMATE_ROOT, "templates", "modules"),                        # third party
    os.path.join(TALEMATE_ROOT, "src", "talemate", "agents"),                   # agent modules
    os.path.join(TALEMATE_ROOT, "src", "talemate", "game", "engine", "nodes", "modules"),
]
```

`templates/modules/` is explicitly the third-party slot (it ships containing only
`put_node_modules_here`). **Another zero-diff extension point.** Note the loader
does two passes with a retry list to handle inter-module dependency order.

An agent package's own `modules/*.json` is picked up automatically because
`src/talemate/agents` is on the search path — this is how e.g.
`agents/visual/modules/generate-visual-asset.json` loads.

### 3.3 Image nodes

`src/talemate/agents/visual/nodes.py` (760 lines) registers 14 nodes, all under
`agents/visual/`. Custom socket types are appended to the shared `TYPE_CHOICES`
list at `nodes.py:50-58` (`visual/prompt`, `visual/generation_request`,
`visual/generation_response`, …).

The pipeline the graph expresses: `PromptPart` → `Prompt` → `ApplyStyles` →
`GenerationRequest` → `GenerateImage` → `UnpackGenerationResponse`.

`GenerateImage` (`nodes.py:623-652`) is the whole executor — it just calls
`self.agent.generate(generation_request)`:

```python
async def run(self, state: GraphState):
    generation_request = self.normalized_input_value("generation_request")
    response = await self.agent.generate(generation_request)
    self.set_output_values({...})
```

`_agent_name: ClassVar[str] = "visual"` on an `AgentNode` is what binds
`self.agent`.

---

## 4. Visual agent, end to end

### 4.1 Composition

`src/talemate/agents/visual/agent.py:53-67` — `VisualAgent` is a mixin stack:

```python
@register()
class VisualAgent(StyleMixin, GenerationMixin, AnalysisMixin,
                  ComfyUIMixin, Automatic1111Mixin, SDNextMixin, GoogleImageMixin,
                  OpenAIMixin, OpenRouterMixin, TalemateClientMixin,
                  OpenAICompatibleMixin, Agent):
    agent_type = "visual"
    verbose_name = "Visualizer"
    essential = False
    websocket_handler = VisualWebsocketHandler
```

Each backend contributes one mixin. `init_actions()` (`:77-215`) builds the config
tree then calls `<Mixin>.add_actions(actions)` for each (`:205-213`).

### 4.2 Three independent backend slots

`_config` (`agent.py:80-151`) declares three separate selections:

| Config key | Attribute | Filter |
|---|---|---|
| `backend` | `self.backend` | `BACKENDS` where `image_create` |
| `backend_image_edit` | `self.backend_image_edit` | where `image_edit` |
| `backend_image_analyzation` | `self.backend_image_analyzation` | where `image_analyzation` |

Plus `timeout` (default 300s), `automatic_setup` (default **True**),
`automatic_generation` (default False).

**Dispatch is by naming convention, not a table.** `evaluate_backend()`
(`agent.py:475-505`) does:

```python
fn = getattr(self, f"{backend_name}_{backend_type}", None)
backend = await fn(old_config=old_config, force=force)
setattr(self, f"{backend_type}", backend)
```

So a backend named `ollama` must supply `ollama_backend` (for text-to-image),
`ollama_backend_image_edit`, and/or `ollama_backend_image_analyzation`. Two more
conventions are looked up the same way: `<name>_prepare_generation`
(`generation.py:186, 210`) and `<name>_emit_status` (`agent.py:558-577`).

### 4.3 Backend base class and the registry

`src/talemate/agents/visual/backends/__init__.py`:

- `BACKENDS: dict` (`:29`), `register(backend_cls)` keyed on `backend.name`
  (`:191-195`) — raises on duplicate.
- `Backend(BackendBase)` (`:37-188`) provides connection-test caching
  (`_test_conn_cache`, class-level, keyed by `status_cache_key`), status→colour
  mapping, and the abstract surface: `ready()`, `test_connection()`,
  `generate(request, response)`, `analyze(request, response)`, `cancel_request()`.

Capability flags are `ClassVar`s on `BackendBase`
(`visual/schema.py:195-214`): `name, label, image_create, image_edit,
image_analyzation, description`.

**The registered backends and their capabilities:**

| Backend | file | create | edit | analyse | base URL configurable? |
|---|---|---|---|---|---|
| `comfyui` | `backends/comfyui.py:299` | ✅ | ✅ | ❌ | ✅ `api_url` |
| `automatic1111` | `backends/automatic1111.py` | ✅ | — | ❌ | ✅ `api_url` |
| `sdnext` | `backends/sdnext.py` | ✅ | — | ❌ | ✅ `api_url` |
| `openai` | `backends/openai_image.py:48` | ✅ | ✅ | ✅ | ❌ **hardcoded** |
| `openai_compatible` | `backends/openai_compatible.py:23` | ❌ | ❌ | ✅ | ✅ `base_url` |
| `google_image`, `openrouter`, `talemate_client` | — | ✅ | varies | varies | n/a (cloud) |

This table is the answer to the brief's Phase 1 question:

- `openai_compatible` **has** a configurable `base_url`
  (`openai_compatible.py:32`) and would happily talk to Ollama — but it declares
  `image_create = False, image_edit = False, image_analyzation = True`
  (`:27-29`) and only implements `analyze()`. It is a **vision/captioning**
  backend, not a generator.
- `openai` **can** generate, but every call constructs its client with no
  `base_url`: `AsyncOpenAI(api_key=get_config().openai.api_key)` at
  `openai_image.py:93, 109, 131, 189`. There is no config field for a URL —
  `openai_shared_config()` (`:242-274`) exposes only `api_key` and `model`.

So repointing is **not** possible without a code change.

### 4.4 Generation flow

`GenerationMixin.generate()` (`visual/generation.py:131-174`):

1. Build `GenerationResponse`, emit `agent.visual.generation.before_generate`.
2. Route on `request.gen_type`: `IMAGE_EDIT` → `generate_image_edit`, else
   `generate_text_to_image`.
3. `generate_text_to_image` (`:176-198`): guard on `can_generate_images`, call
   `<backend>_prepare_generation(request)` to let the mixin mutate resolution /
   stash `agent_config`, then

```python
task = asyncio.create_task(backend.generate(request, response))
task.add_done_callback(lambda fut: asyncio.create_task(on_done(fut)))
self._track_generation_task(task, backend)
await self.set_background_processing(task, self.on_image_generation_error)
```

**Generation is already fully async and non-blocking**, with cancellation support
(`cancel_generation()` at `:225-279` cancels tasks and calls
`backend.cancel_request()`).

4. `on_done` (`:148-165`): auto-save the asset, `emit("image_generated",
   websocket_passthrough=True)`, invoke `request.callback` if set, emit
   `after_generate`.

`GenerationRequest` (`visual/schema.py:280-341`) carries prompt, `gen_type`,
`vis_type`, `format`, `resolution`, `sampler_settings`, `reference_assets`,
a per-request `callback`, and an `AssetAttachmentContext` that decides what the
image gets attached to. It also extracts `{braced}` words from the prompt into
tags via a model validator (`:322-341`).

### 4.5 Asset storage

`src/talemate/scene_assets.py`:

- Directory: `{scene.save_dir}/assets/` (`:462-480`).
- `add_asset()` (`:690-739`) — **content-addressed**: `asset_id =
  sha256(asset_bytes).hexdigest()`, file written as `{asset_id}.{ext}`.
  Identical bytes dedupe automatically (`:707-709`).
- Index: `assets/library.json` (`_library_path :482-487`, load/save `:489-519`),
  holding an `Asset` record per id with `file_type`, `media_type`, and an
  `AssetMeta` (prompt, vis_type, character, resolution, …).
- `add_asset_from_generation_response()` (`:801`) is what the visual agent calls.
- `media_type` is a stored per-asset field — the schema is **not** image-only.

### 4.6 Delivery to the frontend

There is **no HTTP route for assets**. Everything goes over the websocket as
base64:

`src/talemate/server/scene_assets_batching.py:28-47` —

```python
asset = scene_assets.get_asset_bytes_as_base64(asset_id)
self.queue_put({"type": "scene_asset", "asset_id": ..., "asset": asset,
                "media_type": ...})
```

`request_scene_assets()` (`:71-91`) debounces into a 100 ms batch window
(`SCENE_ASSETS_BATCH_WINDOW_SECONDS = 0.1`) and dedupes via a pending set.

**This is the single most important constraint for Phase 3.** Base64 inflates
payloads ~33%; a 10 MB video clip becomes a ~13 MB JSON websocket frame. See
PLAN.md.

### 4.7 ComfyUI backend — the pattern to copy for video

`src/talemate/agents/visual/backends/comfyui.py`:

- `WORKFLOW_DIR = TEMPLATES_DIR / "comfyui-workflows"` (`:37`), module-level
  `WORKFLOW_CACHE` keyed by filename with mtime-based invalidation
  (`_reload_workflow_if_outdated :361-379`, `comfyui_load_workflow :818-845`).
  Ships 5 workflows: `default-sdxl.json`, `default-sd15.json`, `qwen_image.json`,
  `qwen_image_edit.json`, `z_image_turbo.json`.
- **Workflows are bound by node title, not id** — `Workflow` properties
  (`:95-138`) look for `_meta.title` equal to `"Talemate Positive Prompt"`,
  `"Talemate Negative Prompt"`, `"Talemate Resolution"`,
  `"Talemate Load Model"` / `"Talemate Load Checkpoint"`, and
  `"Talemate Reference N"` (1-indexed prefix match). This is the contract when
  you export API-format JSON from ComfyUI.
- Mutators: `set_resolution`, `set_prompt`, `set_main_model`, `set_seeds`
  (randomises any `seed`/`noise_seed` input), `set_reference_images` — the last
  also **disconnects** unpopulated reference nodes by deleting inputs that point
  at them (`:269-296`), which lets one edit-workflow double as a plain generator.
- Transport (`generate :543-646`): POST `/prompt` → get `prompt_id` → poll
  `/history/{prompt_id}` every 1 s until output appears or `generate_timeout`
  (`get_images :468-496`) → fetch bytes from `/view`. Reference images are
  uploaded first via `/upload/image` (`:498-541`). Cancel = POST `/interrupt`
  (`:648-666`).
- Model discovery: GET `/object_info`, parsed by `MODEL_RETRIEVAL_MAP`
  (`:43-47`) which maps loader node types → input key → model type
  (`CheckpointLoaderSimple/ckpt_name/checkpoint`, `UNETLoader/unet_name/unet`,
  `UnetLoaderGGUF/unet_name/unet`).

A video backend is a near-copy of this file with a different workflow dir and an
output node that yields `gifs`/`videos` instead of `images`.

---

## 5. Long-term memory / RAG

### 5.1 The agent

`src/talemate/agents/memory/__init__.py` — abstract `MemoryAgent` (`:59`) plus
`ChromaDBMemoryAgent` (`:725`). ChromaDB is a **synchronous** library, so every
call is pushed off the event loop: `add` uses
`loop.run_in_executor(None, functools.partial(self._add, ...))` (`:370-376`, with
a one-shot retry on `AttributeError` at `:377-410`), `get` uses
`asyncio.to_thread` (`:460`).

Embeddings are preset-driven (`:150-224`): `default`/`sentence-transformer`
(local, `all-MiniLM-L6-v2` on cpu by default), `openai`, or `client-api`
(delegates to an LLM client that reports `supports_embeddings` — Ollama can serve
this). The DB name embeds the embedding fingerprint (`:132-138, 224`), so
**changing embeddings or device forces a full re-import** of the scene
(`handle_embeddings_change :244`).

### 5.2 What gets embedded, and when

Writes are `add`/`add_many` with a `meta` dict carrying at least `typ` and `ts`:

| Source | Site | `typ` |
|---|---|---|
| Archived history (summaries) | `tale_mate.py:1981-2007` | `history` |
| Character sheets / attributes / details | `character.py:448, 483, 517, 607` | character |
| World state entries | `world_state/__init__.py:322, 510` | — |
| Manual world-state entries | `world_state/manager.py:617` | — |
| Game state values | `game/state.py:78` (`uid=game_state.{key}`) | — |

`commit_to_memory` on the scene (`tale_mate.py:1975-2012`) is the full re-index:
`memory.drop_db()` → `set_db()` → re-add all archive entries → each character's
`commit_to_memory` → `world_state.commit_to_memory`.

So: **raw dialogue is not embedded.** What lands in the vector store is
*summarised* history plus structured world/character facts. The summarizer agent
is upstream of memory quality.

### 5.3 Retrieval

`src/talemate/agents/memory/rag.py` — `MemoryRAGMixin`, mixed into the agents
that build prompts. Config action `use_long_term_memory` (`:28-105`) with
`retrieval_method`:

- `direct` (default) — semantic similarity only, no extra LLM call.
- `queries` — +1 LLM call; world_state agent generates similarity queries
  (`analyze_text_and_extract_context_via_queries`, `:263-273`).
- `questions` — +2 LLM calls; compile questions then answer them (`:252-262`).

`semantic_context()` (`:285-342`) is the always-on path: take the last
`num_messages` (default 3) messages of type `character|narrator|director`, split
into sentences, recombine to a `min_query_length` of 100 chars, and run them all
through `memory.multi_query(queries, max_tokens=1024, iterate=5)`.

`rag_build()` (`:175-283`) orchestrates and caches. The cache is **scene-scoped
and cross-agent**, keyed by `retrieval_method-num_queries-answer_length`
(`long_term_memory_cache_key :134-145`) and invalidated by the fingerprint of the
last history message (`rag_get_cache :163-173`).

### 5.4 Where retrieved content lands in the prompt

`src/talemate/prompts/templates/common/memory-context.jinja2` — the whole file:

```jinja
{% set memory_stack = agent_action(active_agent.agent.agent_type, "rag_build",
                                   prompt=memory_prompt, sub_instruction=...) %}
{% if memory_stack %}
<|SECTION:POTENTIALLY RELEVANT INFORMATION|>
{% for memory in memory_stack %}{{ memory|condensed }}
---
{% endfor %}<|CLOSE_SECTION|>
{% endif %}
```

So RAG is pulled **from inside the template** via the `agent_action` helper — the
template drives the retrieval, not the Python caller.

**Ordering — and the merge-conflict hotspot.** In
`prompts/templates/conversation/dialogue.jinja2`, memory is one of three
"volatile" blocks:

```jinja
{% set volatile_context_text %}
{% include "memory-context.jinja2" %}
{% include "world-state-snapshot.jinja2" %}
{% include "extra-context-dynamic.jinja2" %}
{% endset %}
```
(`dialogue.jinja2:47-51`)

and its position is **conditional on a prompt-caching setting**
(`dialogue.jinja2:116, 121-124, 136-139`):

```jinja
{% set volatile_placement = volatile_context_placement() %}
{% if volatile_placement != "after_history" %} … {{ volatile_context_text }} {% endif %}
…
{{ scene_context_text }}
{% if volatile_placement == "after_history" %} … {{ volatile_context_text }} {% endif %}
```

Driven by `optimize_prompt_caching_action()` (`base.py:200-219`), a per-agent
`auto|on|off` override. Note the template also moves the acting instructions
depending on whether scene context exceeds 1024 tokens (`:132, 141`).

The prompt is assembled as `<|SECTION:NAME|> … <|CLOSE_SECTION|>` blocks, with
token budgeting done *in the template* (`count_tokens` on the static and volatile
parts, remainder handed to `scene.context_history(budget=...)`,
`dialogue.jinja2:55-59`).

**This file is exactly the "prompt-context ordering" churn the brief warned
about. We should not touch it.** If we need to inject context, the supported
seams are the `DynamicInstruction` mechanism (`base.py:230-240`, surfaced via
`dynamic-instructions.jinja2`) and template *overrides* — `templates/prompts/<agent>/`
exists for exactly this and is gitignored-adjacent (each dir ships a
`place-template-overrides-here.txt`).

---

## 6. Clients: Ollama, and the image-generation question

### 6.1 The Ollama client

`src/talemate/client/ollama.py`:

- `@register()` from `client/registry.py`, `client_type = "ollama"` (`:32-39`).
- `OllamaClientDefaults.api_url = "http://localhost:11434"` (`:23`) — **base URL
  is per-client-instance config**, editable in the UI, and you can define several
  Ollama clients on different URLs. `Meta.self_hosted = True`,
  `manual_model = True`.
- Status check deliberately bypasses the `ollama` SDK to get a timeout: `httpx`
  GET `{api_url}/api/version` with `timeout=2` (`:124-126`), then
  `fetch_available_models()` (`:138-152`) via `ollama.AsyncClient(host=api_url).list()`,
  cached for `FETCH_MODELS_INTERVAL = 15` s.
- Generation (`:203-237`) uses the **`/api/generate` completion** endpoint with
  `raw=self.can_be_coerced`, streaming, `options["num_ctx"] = max_token_length`.
- `api_handles_prompt_template` (default False) decides whether Talemate applies
  its own prompt template. Leaving it False keeps LLM coercion available
  (`can_be_coerced :92-98`).
- `abort_generation()` is a **no-op** (`:239-245`) — Ollama exposes no abort.

**How "per-agent base URL" actually works:** agents don't own base URLs. For
*text*, an agent points at a named client (`config.agents[x].client`) and the
client owns `api_url`. For *visual backends*, the URL lives in the agent's own
action config (`comfyui_image_create.api_url`,
`openai_compatible_image_analyzation.base_url`) and is per-backend-slot, so the
create/edit/analyse slots can each point somewhere different.

### 6.2 Ollama image generation — tested, and blocked on Windows

Installed locally: **Ollama 0.32.5**, Windows 11.

Route probe (unknown-model POST, to distinguish a missing route from a missing
model):

| Endpoint | Status | Body |
|---|---|---|
| `/v1/chat/completions` | 400 | `"[] is too short - 'messages'"` |
| `/v1/images/generations` | 404 | `"model 'x' not found"` |
| `/v1/models` | 200 | model list |

The images 404 is a **model** error, not a routing error — so **the route
exists** on Windows.

A real attempt with the image model that is installed
(`x/flux2-klein:latest`, 5.33 GB):

```
POST /v1/images/generations {"model":"x/flux2-klein:latest","prompt":"...","size":"512x512"}
→ 500, after 2.3s
{"error":{"message":"mlx runner failed: Error: failed to initialize MLX:
  failed to load MLX dynamic library (searched: [...])"}}
```

**MLX is Apple's Apple-Silicon-only framework.** Ollama routes image generation
to an MLX runner that does not exist on Windows. This is a platform gate, not a
model or config problem — no Talemate-side adapter can work around it.

### 6.3 KoboldCpp auto-setup — already implemented upstream

`src/talemate/client/koboldcpp.py:485-564`. The visual agent's `setup_check()`
(`agent.py:404-427`) probes each backend name for a
`visual_{backend_name}_setup` method **on the client**; KoboldCpp implements
`visual_automatic1111_setup`.

`_visual_automatic1111_setup_impl` (`:516-564`):

1. Bail if already configured for this URL with backend `automatic1111`
   (`:527-535`) — idempotent.
2. GET `{url}/sdapi/v1/sd-models` with `timeout=2`; bail if non-200 or empty
   (`:538-555`).
3. Otherwise set `actions["_config"].config["backend"].value = "automatic1111"`
   and `actions["automatic1111_image_create"].config["api_url"].value = self.url`
   (`:560-563`) and return True — the agent then instantiates the backend and
   emits status (`agent.py:418-426`).

Serialised behind a lock + single task so concurrent status ticks don't race.
Gated by the visual agent's `automatic_setup` config, **which defaults to True**
(`agent.py:137-143`).

**Net: point a KoboldCpp client with a loaded SD model at Talemate and image
generation configures itself. Zero code, zero config.** The same pattern exists
for TTS (`tts_openai_compatible_setup :566`).

---

## 7. Environment / setup friction

Everything below is what actually happened, for reproducibility.

**Deviation from the sanctioned installer.** `install.bat` downloads an *embedded*
Python 3.11.9 and Node 22.22.3 into `embedded_python/` and `embedded_node/`, then
runs `uv sync` and `corepack pnpm install`. I bypassed it and used the system
toolchain, because you already have `uv` and Node. Consequences:

- `start-backend.bat` will not work as-is — it hard-requires
  `embedded_python\python.exe` (`start-backend.bat:21-36`) and will trigger a
  full re-install if run. Use instead:
  `.venv/Scripts/python.exe src/talemate/server/run.py runserver --host 0.0.0.0 --port 5050 --backend-only`
- `start-frontend.bat` shells out to `corepack pnpm run serve` and does work.

**Friction hit:**

1. **Node 25.5.0 does not bundle corepack** (unbundled as of Node 25), and the
   frontend pins `pnpm@11.4.0` via `packageManager`. Fixed with
   `npm i -g corepack@latest`; it installs corepack 0.35.0 which warns
   `EBADENGINE` about Node 25 but works. The project expects Node 22 — **if the
   frontend misbehaves later, Node 25 is the first suspect.**
2. **`uv sync` is heavy** — torch is pinned to the `cu128` index
   (`pyproject.toml:140-151`) and the dependency set includes chromadb,
   sentence-transformers, transformers, and four local TTS engines (chatterbox,
   kokoro, f5-tts, pocket-tts). ~25 min, 445 packages, and the uv cache is now
   **37.6 GB**. Installed `torch 2.11.0+cu128`, CUDA available: **True**.
3. `.python-version` pins 3.11 while system Python is 3.12 — `uv` fetched 3.11.14
   itself. Non-issue, just noting the venv is 3.11.14.
4. **`.idea/` is not in upstream `.gitignore`.** Rather than edit a tracked file
   for our convenience, I added it to `.git/info/exclude`.
5. On first boot the backend **rewrites `config.yaml`** (it is created from
   `config.example.yaml`, then normalised and saved) and **migrates scene assets**,
   writing `library.json` into `scenes/*/assets/`. Both paths are gitignored, so
   the tree stays clean — but don't be surprised by the writes.
6. `uv.lock` is committed; `uv sync` respected it. `pyproject.toml:127` also sets
   `exclude-newer = "1 week"`, so resolution is time-pinned.

**Verified running:** backend on `:5050`, frontend on `:8082`. The UI loads, the
websocket reports `connected`, all agents enumerate, Memory shows green on
ChromaDB + sentence-transformer + `all-MiniLM-L6-v2` + cpu, Visualizer correctly
shows "No backend configured". **Zero console errors.**

Not yet exercised: any actual LLM generation (no client configured in the UI
yet), and `pytest`.
