"""Tests for flow correlation (talemate.flowlog)."""

import structlog
import structlog.testing

import talemate.flowlog as flowlog


class TestFlow:
    def test_flow_binds_and_unbinds_contextvar(self):
        assert "flow" not in structlog.contextvars.get_contextvars()
        with flowlog.flow("visual:visualize") as flow_id:
            assert flow_id.startswith("visual:visualize:")
            assert structlog.contextvars.get_contextvars()["flow"] == flow_id
        assert "flow" not in structlog.contextvars.get_contextvars()

    def test_flow_ids_are_unique(self):
        with flowlog.flow("a") as first:
            pass
        with flowlog.flow("a") as second:
            pass
        assert first != second

    def test_summary_aggregates_calls(self):
        with structlog.testing.capture_logs() as captured:
            with flowlog.flow("director:chat_send"):
                flowlog.record_llm_call(agent="director", duration=2.0)
                flowlog.record_llm_call(agent="director", duration=1.5)
                flowlog.record_llm_call(agent="narrator", duration=3.0)

        summaries = [e for e in captured if e["event"] == "flow.summary"]
        assert len(summaries) == 1
        summary = summaries[0]
        assert summary["llm_calls"] == 3
        assert summary["llm_seconds"] == 6.5
        assert summary["agents"] == {"director": 2, "narrator": 1}

    def test_no_summary_without_calls(self):
        with structlog.testing.capture_logs() as captured:
            with flowlog.flow("visual:checkpoints"):
                pass
        assert not [e for e in captured if e["event"] == "flow.summary"]

    def test_record_outside_flow_is_noop(self):
        flowlog.record_llm_call(agent="director", duration=1.0)

    def test_unattributed_calls_counted(self):
        with structlog.testing.capture_logs() as captured:
            with flowlog.flow("x"):
                flowlog.record_llm_call(agent=None, duration=None)
        summary = [e for e in captured if e["event"] == "flow.summary"][0]
        assert summary["agents"] == {"unattributed": 1}
        assert summary["llm_seconds"] == 0

    def test_exception_inside_flow_still_unbinds(self):
        try:
            with flowlog.flow("boom"):
                raise ValueError("x")
        except ValueError:
            pass
        assert "flow" not in structlog.contextvars.get_contextvars()


class TestNestedFlows:
    def test_nested_flow_restores_outer_flow_key(self):
        with flowlog.flow("outer") as outer_id:
            with flowlog.flow("inner") as inner_id:
                assert structlog.contextvars.get_contextvars()["flow"] == inner_id
            assert structlog.contextvars.get_contextvars()["flow"] == outer_id
        assert "flow" not in structlog.contextvars.get_contextvars()

    def test_nested_calls_attribute_to_inner_flow(self):
        with structlog.testing.capture_logs() as captured:
            with flowlog.flow("outer"):
                flowlog.record_llm_call(agent="a", duration=1.0)
                with flowlog.flow("inner"):
                    flowlog.record_llm_call(agent="b", duration=2.0)
        summaries = [e for e in captured if e["event"] == "flow.summary"]
        assert len(summaries) == 2
        inner, outer = summaries
        assert inner["agents"] == {"b": 1}
        assert outer["agents"] == {"a": 1}
