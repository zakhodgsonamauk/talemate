"""
Retry behaviour of `prompt/GenerateResponse` when extraction fails.

Observed live: a 12B returned a visual prompt missing the required `descriptive`
section, `ResponseSpec.extract_all` raised, and the whole visualize action aborted
before ComfyUI was ever contacted. The node's `attempts` property was wired only to
*empty* responses, so a non-empty but unparseable one got no second chance.

These tests exercise the retry decision directly rather than standing up a full graph:
the node's send loop is thin, and what matters is that an extraction failure consumes
an attempt, that a later success is used, and that exhausting the attempts still
surfaces the original error rather than silently continuing.
"""

import pytest

from talemate.prompts.response import ExtractionError


GOOD = "<KEYWORDS>a, b</KEYWORDS><DESCRIPTIVE>a room</DESCRIPTIVE>"
BAD = "<KEYWORDS>a, b</KEYWORDS>"


class _Spec:
    """Stands in for a ResponseSpec: raises unless the response has both sections."""

    extractors: dict = {}

    def __init__(self):
        self.calls = 0

    def extract_all(self, response):
        self.calls += 1
        if "DESCRIPTIVE" not in response:
            raise ExtractionError("Required field 'descriptive' not found in response")
        return {"keywords": "a, b", "descriptive": "a room"}


def _run(responses, spec, attempts=1):
    """Mirror of the node's send loop, so the decision logic is what is tested."""
    send_attempts = max(attempts, 2) if spec is not None else attempts
    sent = 0
    extracted = None
    error = None

    for _ in range(send_attempts):
        response = responses[min(sent, len(responses) - 1)]
        sent += 1

        if not response:
            continue
        if spec is None:
            break
        try:
            extracted = spec.extract_all(response)
        except ExtractionError as exc:
            error = exc
            continue
        error = None
        break

    if error is not None:
        raise error
    return extracted, sent


def test_a_malformed_response_is_retried():
    """The observed failure: first response missing a section, second one fine."""
    spec = _Spec()
    extracted, sent = _run([BAD, GOOD], spec)
    assert extracted["descriptive"] == "a room"
    assert sent == 2, "the malformed response did not consume an attempt"


def test_a_good_first_response_costs_only_one_call():
    """Retry must not add a call to the happy path."""
    spec = _Spec()
    extracted, sent = _run([GOOD], spec)
    assert extracted is not None
    assert sent == 1


def test_exhausting_the_attempts_raises_the_original_error():
    """Silently continuing with nothing extracted would hide the failure."""
    spec = _Spec()
    with pytest.raises(ExtractionError, match="descriptive"):
        _run([BAD, BAD], spec)


def test_extraction_gets_an_allowance_even_when_the_graph_asks_for_one_attempt():
    """`generate-visual-asset.json` hardcodes attempts: 1, which is the failing path."""
    spec = _Spec()
    extracted, sent = _run([BAD, GOOD], spec, attempts=1)
    assert extracted is not None
    assert sent == 2


def test_a_graph_asking_for_more_attempts_keeps_them():
    spec = _Spec()
    with pytest.raises(ExtractionError):
        _run([BAD, BAD, BAD], spec, attempts=3)
    assert spec.calls == 3


def test_no_response_spec_means_no_extra_attempt():
    """Nothing to parse, so nothing to retry for - behaviour is unchanged."""
    _, sent = _run([GOOD], None, attempts=1)
    assert sent == 1


# === the allowance, and what is actually required ===


def test_extraction_allowance_is_three():
    """Two proved too few: a 12B missed the same section twice in a row."""
    from talemate.game.engine.nodes.prompt import EXTRACTION_ATTEMPTS

    assert EXTRACTION_ATTEMPTS == 3


def test_three_attempts_are_used_before_giving_up():
    spec = _Spec()
    with pytest.raises(ExtractionError):
        _run([BAD, BAD, BAD], spec, attempts=3)
    assert spec.calls == 3


def test_the_visual_prompt_no_longer_requires_the_descriptive_half():
    """`descriptive` is unused downstream in KEYWORDS mode.

    `anchors.py:434-437` says so outright, and `characters_in_frame` already falls
    back to every supplied character when the prose is absent. Failing an entire
    visualize because an optional hint was missing was the real defect; the retry
    only treated the symptom.
    """
    import json
    from pathlib import Path

    graph = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "src/talemate/agents/visual/modules/generate-visual-asset.json"
        ).read_text(encoding="utf-8")
    )
    raw = graph["nodes"]
    nodes = raw if isinstance(raw, dict) else {n["id"]: n for n in raw}

    specs = [
        n for n in nodes.values() if n.get("registry") == "response/ResponseSpec"
    ]
    assert specs, "no ResponseSpec node in the visual-asset graph"

    for spec in specs:
        required = spec["properties"].get("required") or []
        assert "keywords" in required, "keywords must stay required - it is the prompt"
        assert "descriptive" not in required, (
            "descriptive is required again; a missing optional hint will abort visualize"
        )
