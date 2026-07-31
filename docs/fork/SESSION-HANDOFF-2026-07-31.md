# Session handoff — 2026-07-31

Paste this into a new session as context. Everything below is verified, not assumed.

## Repo state

Branch `feature/tell-me-a-story`, clean and pushed (26 commits today, `f7d4616` … `4df2454`).
Three conductor tracks, all under `conductor/tracks/`:

| track | done | notes |
| --- | --- | --- |
| `autonomous-story` | 11/30 | story runs itself; cloud director live |
| `visual-subject-fidelity` | 10/19 | image shows the right person doing the right thing |
| `visualize-prompt-adjustment` | 8/11 | **adopted** from a closed session |

## THE IMMEDIATE PROBLEM — prompt generation is wrong in four ways at once

Reported at the end of the session, not yet investigated:

1. **Prompt is too long.**
2. **Missing the key parts** — character pose, clothing.
3. **Censored when it should not be** — the character is half-naked in the scene.
4. **Self-contradictory** — the same prompt contains **`rating_safe`** *and*
   **"exposed breasts with hardened nipples"**.

Point 4 is diagnostic. `rating_safe` is added by `_add_rating_tags`
(`src/talemate/agents/visual/generation.py`), gated on `_subject_is_dressed(keywords)`.
That helper counts keywords: classify each once, undress-intent before clothing, then
`dressed = garments > undress`. With trousers + boots + harness present and only one or two
undress phrases, it concludes "dressed" and applies `rating_safe` plus the whole
`NUDITY_NEGATIVES` set — on top of an LLM prompt that is explicitly describing nudity.

**Do not patch the counting again.** It has now failed three times in both directions:
- a bare `bare` matched "bare metal walls" (false undress)
- another character's leaked "bare chest" disarmed every nudity negative (false undress)
- garments outnumbering undress phrases censored an explicit scene (false dressed)

The counting heuristic is the wrong instrument and is already scoped for replacement as
**`visual-subject-fidelity` T16**, which is the right next move:

> Read the answer from the **subject**, not from the whole prompt.
> `character.visual_wardrobe` (`agents/visual/anchors.py:41`, `WARDROBE_QUESTION`, refreshed
> every `DEFAULT_WARDROBE_INTERVAL` = 10 turns) is an LLM-written statement of what *that*
> character is wearing. `_suppress_stale_wardrobe` already establishes the precedence: what
> the scene says now beats the cached reinforcement, because undressing is a single beat the
> reinforcement will not notice for several more. Because it is scoped to one character,
> another character's traits can never reach it. Escalate to an explicit LLM judgement only
> when those sources are absent or conflict. Keep counting as a last-resort fallback only,
> and say so in the code.

`_subject_is_dressed` is deliberately a single helper so this replacement is one edit. It has
two callers: `_add_rating_tags` and `_add_sex_negatives`.

Points 1–3 are already scoped as `visual-subject-fidelity` **T6, T7, T8**:
- **T6** drop vocabulary that cannot be drawn. ~40 tokens of an observed prompt were abstract
  (`wrongness`, `hypnotic cluster`, `chaotic noise`, `determined`, `professional`,
  `problem solving`, `time pressure`). None of it draws; all of it crowds out what does.
- **T7** keep the action as one weighted phrase. "leaning over the console" arrives as
  `standing, lean, hands hovering, interface` — the subject-object relationship destroyed by
  comma splitting, and unweighted while the appearance anchor carries `:1.3`.
- **T8** reduce the token budget, **measured**. `image_max_tokens` is `250` in config;
  `DEFAULT_MAX_PROMPT_TOKENS` is `77` (one CLIP chunk). An observed prompt measured ~196
  tokens with the action words near token 95. `style.py:39` already records that past ~155
  tokens roughly half the prompt is beyond where the encoder meaningfully attends.

## Environment facts that cost time to learn

- **Run the app**: `start-fork.bat` (needs `.\` prefix from cmd — a bare name with two dots
  fails to resolve). ComfyUI is now the default image backend; `USE_KOBOLDCPP=1` for the
  fallback. `start-fork.local.bat` is now tracked and holds machine paths.
- **The launcher sets `TALEMATE_DEBUG=1`, `PYTHONIOENCODING=utf-8`, `PYTHONUNBUFFERED=1`.**
  Without the encoding var a redirected backend dies on its own startup banner. Without
  debug you get no `choose_subject`, `sex_tags.added`, `rating_tags.added` or
  `finalize_prompt` lines, and a wrong image gives you nothing to work from.
- **Frontend changes need `pnpm build`.** `frontend_wsgi.py:17` serves
  `talemate_frontend/dist`, so editing a `.vue` file changes nothing at runtime. `dist` is
  gitignored. Bundle names are content-hashed — hard-refresh the browser.
- **Config is rewritten by the running app.** Do not hand-edit `config.yaml` while Talemate
  is running; it also re-sorts keys, so text-anchored edits break.
- **`config.yaml` is gitignored**, so the cloud-director setup is machine-local. A documented
  example lives in `config.example.yaml`.
- **Tests**: `.venv/Scripts/python.exe -m pytest -q`. 5842 pass. **7 pre-existing failures**
  are Windows/time artefacts — `%-` strftime, `\` vs `/` paths, a template test reading
  without `encoding='utf-8'`, and two timestamp-flaky director tests. Not yours; two of them
  flip between runs.
- **Sub-agents returned empty three times** in this session (`loop-plan-evaluator`,
  `cto-plan-reviewer`, a `general-purpose` investigator). Doing the work inline was faster
  and produced better evidence. Do not rely on them here.
- **GPU budget**: RTX 4080, 16GB. Rocinante-12B holds ~7.5GB and ComfyUI is launched with
  `--reserve-vram 5`, so the image model realistically has ~8–9GB.

## What was fixed today, so you do not redo it

**Autonomous story** — the 3-beat cap was a hardcoded `"3"` in `scene-loop.json` whose own
watch node was named `EMERGENCY BREAK`; now `game.general.max_ai_turns`, default 12, with a
quick setting. Direct-address hand-back (`scene/address.py`, `scene/PlayerWasAddressed`) is
merged with the director's yield through one writer — stage 0 writes the cap and stage 2
overwrites it, so two independent writers would race. Dice ask before resolving the player's
own rolls. Director routed to `deepseek-v4-pro:cloud` with `abstract_context` on, so no prose
leaves the machine. **`reason_enabled` needs `reason_failure_behavior: ignore` with Ollama** —
Talemate expects inline `<think>` tags, Ollama returns thinking in a separate field, and the
default `fail` raises on every turn.

**Visual** — sex conditioning via booru tags (`1boy`/`1girl`) plus opposite-sex negatives;
`character_sex` falls back to appearance prose because a character generated in play had no
`gender` key at all. Reference conditioning switched to `ip-adapter-plus-**face**`, which
fixed an outdoor reference photo putting a reactor scene in a garden. Another character's
species and gear no longer leak (matching widened from the anchor to `base_attributes`).
`visual-character` messages now request `SCENE_ILLUSTRATION`, not `CHARACTER_CARD`.
Extraction retries up to `EXTRACTION_ATTEMPTS = 3`, and `descriptive` is no longer required
because nothing downstream needs it.

**Adopted track** — Adjust & Visualize now asks what the image should be *before* composing
the prompt (`SceneMessages.vue`, `visTypeDialog`). The automatic guess is the default, not the
decision. Plain Visualize is unchanged. Also fixed: the prompt-only path reached
`Finalize Visual Prompt` with `generation_request=None`, because the request was stored from
`Generate Image`, which is gated on NOT `prompt_only`.

## Known-unfixed, deliberately

- **Attachment.** Images do not embed next to their paragraph because
  `appearance.scene.auto_attach_assets` is **False** (Settings → Appearance → Assets →
  "Auto-attach visuals"). `scene_assets.py:367` gates `smart_attach_asset` on it **even when
  the attachment context names explicit `message_ids`**. Arguably the real defect: "auto
  attach" reads like it should govern auto-*detection*, not an attach you requested by
  visualising a specific paragraph. User was flipping the setting; unverified.
- **T14, model profiles.** Justified on evidence — payload inspection proved booru sex tags,
  13 anatomy negatives, the rating axis and cfg 8 were all delivered to ComfyUI and ignored by
  `CyberRealisticPony_V9`. But a profile is checkpoint **+ sampler + style tags**: Pony wants
  score tags with steps 30 / cfg 5–7 / DPM++ 2M Karras, `sd_xl_turbo` wants no score tags with
  ~6 steps / cfg ~1. Never raise cfg on the distilled workflows (`qwen_image`,
  `z_image_turbo`) — it degrades them. Model research and links are in the session log; Z-Image
  Turbo FP8 (~8GB) fits the VRAM budget and Talemate already ships a `z_image_turbo.json`
  workflow, but moving off SDXL abandons the whole IPAdapter identity path.
- **Score tags** are currently dropped for dressed subjects as an experiment
  (`SCORE_TAGS`, `_drop_score_tags_when_dressed`). If T16 changes the dressed determination,
  re-evaluate whether this is still wanted.

## Suggested order

1. **T16** — replace `_subject_is_dressed` with the per-character wardrobe signal. Fixes the
   `rating_safe` + "exposed breasts" contradiction at its cause.
2. **T6** — cull unrenderable vocabulary. Cheap, recovers ~40 tokens.
3. **T7** — action as one weighted phrase. This is what the user does by hand today.
4. **T8** — reduce the budget, from the measurement T6/T7 produce.
5. Only then **T14**, and only if a fair test still fails.

Verify by generation at each step and read the **payload actually delivered to ComfyUI** from
the backend log, not the UI — `grep "comfyui.Backend.generate"` and parse the `payload=` dict.
That is the only ground truth; the UI has agreed with the payload every time, but the payload
is what settled every real question today.
