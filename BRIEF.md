# Project Brief — Talemate fork

Read this file fully before doing anything. Then follow the phases in order.
**Do not skip to writing code.** Phase 0 is mandatory and ends with you stopping.

---

## What we're building

An AI-driven interactive storytelling engine (D&D-DM style narration) that runs
locally, uses **Ollama** for text, has **retrieval-backed world consistency**, and
generates **scene images** — and eventually **short video clips** — inline as the
story progresses.

Rather than building this from scratch, we are forking
[`vegu-ai/talemate`](https://github.com/vegu-ai/talemate), which already provides
the hard part: an agent architecture (narrator, director, world state, summarizer,
visual), ChromaDB long-term memory, pinned context, and a node editor. Ollama is
already a supported client. Image generation already exists via ComfyUI /
Automatic1111 / SD.Next / OpenAI-compatible backends.

**What's missing and what we're adding is deliberately small.** Our job is to add
features at the edges, not to re-architect. If you find yourself wanting to rewrite
a core subsystem, stop and ask.

Docs: <https://vegu-ai.github.io/talemate/> — read these, don't guess at the design.

---

## Hard constraints

- **Licence is AGPL-3.0.** Keep the licence file, keep attribution, note our
  modifications. Don't pull in incompatible dependencies.
- **Minimise core diffs.** Upstream ships fast and makes breaking changes (ports,
  env var names, prompt-context ordering). Every line we change in an existing file
  is a future merge conflict. Strongly prefer new agents, new node modules, new
  files. Where a core file must be touched, make the smallest possible hook.
- **Track upstream from day one.** Fork on GitHub, clone our fork, add `upstream`
  pointing at `vegu-ai/talemate`, and keep our work on a feature branch off `main`.
- **You cannot verify GPU/model work yourself.** Anything requiring a running LLM,
  diffusion model, or ComfyUI instance gets handed to me to test. Write the code,
  write clear manual test steps, then stop and ask.
- **No secrets in the repo.** API keys and endpoints go in `.env` / local config.

---

## Environment

- Ollama is (or will be) running locally on the default port.
- ComfyUI is **not** set up and I'd like to defer it as long as possible.
- Backend is Python/FastAPI managed with `uv`; frontend is Vue with npm.
- Tell me if you need me to install or start something — don't work around a
  missing service by mocking it silently.

---

## Phase 0 — Recon (do this first, then STOP)

1. Fork and clone into this folder. Set up the `upstream` remote. Record the exact
   upstream commit SHA we forked from.
2. Get the dev environment installed and the app running (backend + frontend).
   Report any setup friction you hit.
3. Read the codebase properly. Specifically map out:
   - How an **agent** is defined, registered, configured, and invoked. What's the
     minimum viable new agent?
   - How the **visual agent** works end to end: config schema, backend selection,
     the ComfyUI/A1111/OpenAI adapters, where generated assets are stored, and how
     they get to the frontend.
   - How **image generation nodes** work in the node editor, and how a new node type
     is registered.
   - How **long-term memory / RAG** works: what gets embedded, when, what's
     retrieved, and where retrieved content lands in the prompt.
   - How the **Ollama client** is implemented, and whether backend base URLs are
     user-configurable per-agent.
4. Write two files in the repo root:
   - `ARCHITECTURE.md` — what you learned above, with file paths and line
     references. This is the map we both work from. Be concrete, not summary-ish.
   - `PLAN.md` — a phased implementation plan for the work below, with your own
     estimate of difficulty and risk per item, and explicit flags on anything you
     think is a bad idea or where the codebase resists the approach.
5. **Stop and wait for my review.** Do not start Phase 1.

---

## Phase 1 — Images without ComfyUI

Goal: scene images generated inline, with no ComfyUI dependency.

Ollama now exposes an OpenAI-compatible `/v1/images/generations` endpoint
(experimental, macOS-first — confirm current platform support before building on it).
Talemate already has an OpenAI visual backend.

- First, determine whether the existing OpenAI visual backend can simply be
  **repointed** at Ollama via a configurable base URL. If yes, this phase is a
  config change plus docs, not a feature. Say so and don't pad it.
- If not, add a minimal `ollama` visual backend adapter following the existing
  adapter pattern exactly.
- Either way: document the setup in `docs/` and give me manual test steps.

Also evaluate and write up (don't implement yet) the KoboldCpp fallback path —
a single binary serving a GGUF text model plus a diffusion model, which Talemate
reportedly auto-detects for the Visual agent. Useful if the Ollama path is blocked
on platform support.

---

## Phase 2 — My feature list

<!-- FILL THIS IN before running Phase 2. One line per feature. -->
<!-- e.g. dice mechanics, party/NPC tracking, session recap on load, save/branch -->

TBD — I'll add these after reviewing your `ARCHITECTURE.md`.

---

## Phase 3 — Video agent (last, do not start early)

A `video` agent sitting parallel to the existing visual agent, generating short
clips (Wan / LTX via ComfyUI API workflows) for key story beats.

- Mirror the visual agent's structure closely — config schema, backend adapter,
  asset storage, node registration. Copy the pattern, don't invent one.
- ComfyUI is used **headless**: I build the workflow once and export API-format
  JSON; the agent posts to it. Talemate already loads API workflow files from
  `./templates/comfyui-workflows` — follow that convention.
- Generation takes tens of seconds, so it must be **async and non-blocking**. The
  story loop never waits on a video. Assume the clip arrives late and attaches to
  its scene beat retroactively.
- This is opt-in and off by default.

---

## Working style

- Small, focused commits with clear messages. No giant omnibus commits.
- Maintain `FORK.md`: a running list of every upstream file we've modified and why.
  This is our merge-conflict early warning system.
- When something is ambiguous, ask me rather than guessing — especially about
  product behaviour and anything touching prompt construction.
- If you discover upstream already does something we planned to build, tell me
  immediately and we'll cut it from scope.
- Push back if you think an instruction in this brief is wrong. You'll have read
  more of the code than I have.
