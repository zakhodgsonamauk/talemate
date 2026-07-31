# Review notes — director-trust-and-levers

Findings from the per-phase second-opinion reviews and their dispositions.

## Phase 1 — nospoilers contract v2 + redaction gate (featherweight, job 20260731-211016-dfd7)

- **"BLOCKING": prepared_response prepending breaks the redaction gate** — DISPUTED,
  addressed by documentation + test instead of the suggested fix. The reviewer read the
  prefill (`set_prepared_response("<REDACTED>")`) as artificially satisfying the regex.
  Prefill coercion is the codebase-standard mechanism (chat.jinja2 prefills `<MESSAGE>`);
  the completion after the prefill IS the redacted reply by construction. The suggested
  fix (drop prefill, require strict closing tag) would make the gate fail OPEN — display
  the unredacted original whenever the model omits tags — which is the worst failure mode
  for a spoiler gate. Kept prefill + `$` fallback; documented the rationale on
  `REDACTED_PATTERN` in `chat/mixin.py`; added
  `test_prefill_coerced_response_without_tags` covering the exact scenario the reviewer
  flagged.
- **"BLOCKING": regex allows missing closing tag via `$`** — same issue/disposition as
  above (deliberate, now documented).
- **NON-BLOCKING: model_copy swap "fragile", wants comment** — comment already present on
  `_serialize_chat_message_for_prompt` ("Copies the model - the stored (redacted) message
  is never mutated"); no change.
- **NON-BLOCKING: tests missed the prepend path** — accepted; test added (see above).
- Schema backward-compat, message drop/duplication, template structure: reviewer verified
  OK.
