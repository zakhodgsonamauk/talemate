"""Unit tests for narrator node input handling.

Regression: unconnected optional inputs (e.g. narrative_direction on the
auto-narration path) resolve to the UNRESOLVED sentinel, which leaked into
prompts as "<class 'talemate.game.engine.nodes.core.UNRESOLVED'>" and made
some models return empty responses. prepare_input_values must drop
UNRESOLVED values so agent-function defaults apply.
"""

from __future__ import annotations

import pytest

from talemate.agents.narrator.nodes import GenerateAfterDialogNarration
from talemate.game.engine.nodes.core import UNRESOLVED


@pytest.mark.asyncio
async def test_prepare_input_values_drops_unresolved(monkeypatch):
    node = GenerateAfterDialogNarration()
    # simulate the auto-narration graph: character connected, direction not
    values = {
        "character": "mock-character",
        "narrative_direction": UNRESOLVED,
        "response_length": 0,
    }
    monkeypatch.setattr(
        GenerateAfterDialogNarration,
        "get_input_values",
        lambda self: dict(values, state="mock-state"),
    )

    prepared = await node.prepare_input_values()

    assert "state" not in prepared
    assert "narrative_direction" not in prepared
    assert prepared["character"] == "mock-character"
    assert prepared["response_length"] == 0
