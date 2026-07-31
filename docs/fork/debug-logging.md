# Debug logging

Every structlog event — including debug-level agent decision events that never
reach the console — is written to a rotating JSONL file:

```
logs/talemate-debug.jsonl        (relative to the talemate project root)
```

Console output keeps its previous level and format. The only visible console
change: lines emitted inside a websocket action or game-loop round carry
`flow=...` / `round=...` keys.

## Config (`config.yaml`)

```yaml
debug_log:
  enabled: true
  path: logs/talemate-debug.jsonl   # relative paths resolve against project root
  level: DEBUG
  max_mb: 20
  backups: 5
```

Requires a backend restart to change.

## What gets logged

- Everything the console logs, as JSON, plus all debug-level events
  (`choose_subject`, `attach_character_references.*`, `distill_prompt.*`,
  focal extraction warnings, ...).
- `llm.call.completed` — one per LLM generation: `agent`, `agent_action`,
  `agent_stack`, `client`, `model`, `kind`, `prompt_tokens`,
  `response_tokens`, `duration`, `tokens_per_second`.
- `flow.summary` — one per websocket action that made LLM calls:
  `llm_calls`, `llm_seconds`, `agents` (per-agent call counts).
- `flow` key on every line inside a websocket action
  (`visual:visualize:ab12cd`), `round` key on every line inside a game-loop
  round.

## Recipes

Python one-liners are the most portable on Windows (jq works too if
installed). All examples read `logs/talemate-debug.jsonl`.

**Everything one visualize click did** (find the flow id, then filter):

```bash
grep "visual:visualize" logs/talemate-debug.jsonl | head -1
grep "visual:visualize:ab12cd" logs/talemate-debug.jsonl
```

**All LLM calls in a turn, with timing** :

```bash
grep llm.call.completed logs/talemate-debug.jsonl | python -c "
import json,sys
for line in sys.stdin:
    d=json.loads(line)
    print(f\"{d.get('timestamp','')} {d.get('flow','-'):40} {str(d.get('agent')):12} {str(d.get('agent_action')):28} {d.get('duration')}s {d.get('prompt_tokens')}->{d.get('response_tokens')}\")"
```

**Slowest calls:**

```bash
grep llm.call.completed logs/talemate-debug.jsonl | python -c "
import json,sys
rows=[json.loads(l) for l in sys.stdin]
for d in sorted(rows,key=lambda d:d.get('duration') or 0)[-15:]:
    print(d.get('duration'),d.get('agent'),d.get('agent_action'),d.get('kind'),d.get('flow'))"
```

**Flow cost summaries (which actions are expensive):**

```bash
grep flow.summary logs/talemate-debug.jsonl | tail -20
```

**Decision events for the visual pipeline:**

```bash
grep -E "choose_subject|attach_character_references|distill_prompt|subjects_by_anchor" logs/talemate-debug.jsonl
```

**Follow live during play:**

```bash
tail -f logs/talemate-debug.jsonl | grep --line-buffered -E "llm.call.completed|flow.summary"
```

## Caveats

- Flow summaries are best-effort for handlers that spawn background tasks:
  calls recorded after the websocket dispatch returns keep their `flow` tag
  (authoritative) but may miss that flow's summary line.
- Rotation keeps `max_mb × (backups+1)` on disk; the current file is always
  `talemate-debug.jsonl`.
- The sink never raises: if the file can't be opened, behavior degrades to
  console-only.
