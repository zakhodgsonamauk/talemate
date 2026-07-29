# FORK — divergence from upstream

Merge-conflict early warning system. Every upstream file we modify gets a row
here, with why. New files we add are listed separately — they carry no merge risk.

**Upstream:** [`vegu-ai/talemate`](https://github.com/vegu-ai/talemate) (AGPL-3.0)
**Our fork:** `zakhodgsonamauk/talemate`
**Fork point:** `c12a82930e913816fdac21aedada1962ac45c3d7` — tag `0.38.0`, 2026-07-02
**Working branch:** `feature/tell-me-a-story` off `main`

Keep `main` a clean mirror of `upstream/main`. Sync with:

```bash
git fetch upstream
git checkout main && git merge --ff-only upstream/main
git checkout feature/tell-me-a-story && git rebase main   # or merge
```

---

## Modified upstream files

**None yet.** Phase 0 was recon only.

| File | Lines touched | Why | Added |
|---|---|---|---|
| — | — | — | — |

When this table gains rows, note the *upstream* line numbers at time of change so
a future rebase can find the hunk.

---

## Files we added

These do not conflict — but they can be *orphaned* by upstream refactors, so the
"depends on" column matters.

| Path | Purpose | Depends on (upstream contract) |
|---|---|---|
| `BRIEF.md` | Project brief | — |
| `ARCHITECTURE.md` | Subsystem map from Phase 0 recon | Line refs valid at `0.38.0` — restate on major bumps |
| `PLAN.md` | Phased implementation plan | — |
| `FORK.md` | This file | — |

---

## Local-only, deliberately not committed

- `.git/info/exclude` — holds `.idea/`. Upstream's `.gitignore` doesn't cover it,
  and we don't want to modify a tracked file just for editor noise.

---

## Licence obligations (AGPL-3.0)

- `LICENSE` stays as-is, unmodified.
- Upstream attribution stays in `README.md`.
- Our modifications must be noted — this file is that record.
- Any new dependency must be AGPL-compatible. Check before adding.

---

## Known extension points that cost ZERO upstream diff

Prefer these. Established during recon (see `ARCHITECTURE.md` for detail):

| Want to add | Where | Mechanism |
|---|---|---|
| A new agent | `src/talemate/agents/custom/<name>/` | Auto-imported (`agents/custom/__init__.py:24-34`) |
| A new LLM client | `src/talemate/client/custom/` | Same pattern |
| Declarative node modules | `templates/modules/` | On `SEARCH_PATHS` (`nodes/__init__.py:10-19`) |
| An agent's own node modules | `<agent pkg>/modules/*.json` | `src/talemate/agents` is on `SEARCH_PATHS` |
| Prompt template overrides | `templates/prompts/<agent>/` | Ships `place-template-overrides-here.txt` |
| ComfyUI workflows | `templates/comfyui-workflows/` | Loaded by filename (`comfyui.py:37`) |
| Agent settings UI | nothing — declare `AgentAction`s | Rendered generically from `config_options()` |

## Known places that DO require a diff

| Want to add | Minimum diff | Note |
|---|---|---|
| A visual backend | 3 lines in `src/talemate/agents/visual/agent.py` | import + mixin base + `add_actions()` call. No plugin discovery for backends. |
| An HTTP asset route | new file in `src/talemate/server/` + 1 router include | Additive — the good kind of diff |

## Do not touch

- `src/talemate/prompts/templates/conversation/dialogue.jinja2` — volatile-context
  ordering flips on a prompt-caching setting and upstream actively churns it.
  Use `DynamicInstruction` or a template override instead.
