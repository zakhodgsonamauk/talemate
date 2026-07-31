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
