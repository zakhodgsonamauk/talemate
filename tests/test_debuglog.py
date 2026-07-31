"""Tests for the structured JSONL debug file sink (talemate.debuglog)."""

import json
import logging
from types import SimpleNamespace

import pytest
import structlog

import talemate.debuglog as debuglog
from talemate.config.schema import DebugLogConfig


@pytest.fixture(autouse=True)
def reset_structlog():
    """Each test starts from structlog defaults and leaves them behind."""
    debuglog._reset_for_tests()
    yield
    debuglog._reset_for_tests()


def _configure(tmp_path, console_level=logging.INFO, **overrides):
    cfg = DebugLogConfig(
        path=str(tmp_path / "debug.jsonl"), **overrides
    )
    debuglog.configure_structlog(console_level=console_level, debug_log=cfg)
    return tmp_path / "debug.jsonl"


def _read_lines(path):
    return [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class TestFileSink:
    def test_debug_and_info_events_reach_file_as_valid_jsonl(self, tmp_path):
        path = _configure(tmp_path)
        log = structlog.get_logger("test")

        log.debug("decision event", subject="kaira", source="choose_subject")
        log.info("Sending prompt", token_length=123)

        lines = _read_lines(path)
        assert len(lines) == 2
        parsed = [json.loads(line) for line in lines]
        assert parsed[0]["event"] == "decision event"
        assert parsed[0]["subject"] == "kaira"
        assert parsed[0]["level"] == "debug"
        assert parsed[1]["event"] == "Sending prompt"
        assert parsed[1]["token_length"] == 123
        # default TimeStamper is upstream of the tee
        assert "timestamp" in parsed[0]

    def test_utf8_content_survives_roundtrip(self, tmp_path):
        path = _configure(tmp_path)
        structlog.get_logger("test").debug(
            "unicode check", note="emoji \U0001f409 and dash —"
        )

        parsed = json.loads(_read_lines(path)[0])
        assert parsed["note"] == "emoji \U0001f409 and dash —"

    def test_unserializable_values_fall_back_to_str(self, tmp_path):
        path = _configure(tmp_path)

        class Opaque:
            def __repr__(self):
                return "<opaque>"

        structlog.get_logger("test").debug("odd value", value=Opaque())

        parsed = json.loads(_read_lines(path)[0])
        assert parsed["value"] == "<opaque>"

    def test_exception_rendered_into_file_line(self, tmp_path):
        path = _configure(tmp_path)
        log = structlog.get_logger("test")
        try:
            raise ValueError("boom")
        except ValueError:
            log.error("operation failed", exc_info=True)

        parsed = json.loads(_read_lines(path)[0])
        assert "ValueError: boom" in parsed["exception"]

    def test_rotation_at_configured_size(self, tmp_path):
        # duck-typed config so the test can use a tiny rotation threshold
        path = tmp_path / "debug.jsonl"
        cfg = SimpleNamespace(
            enabled=True,
            path=str(path),
            level="DEBUG",
            max_mb=2048 / (1024 * 1024),  # 2 KB
            backups=2,
        )
        debuglog.configure_structlog(console_level=logging.INFO, debug_log=cfg)
        log = structlog.get_logger("test")
        for i in range(60):
            log.debug("filler", i=i, padding="x" * 100)

        assert path.exists()
        assert (tmp_path / "debug.jsonl.1").exists()

    def test_file_level_respected(self, tmp_path):
        path = _configure(tmp_path, level="INFO")
        log = structlog.get_logger("test")
        log.debug("below file level")
        log.info("at file level")

        parsed = [json.loads(line) for line in _read_lines(path)]
        assert [p["event"] for p in parsed] == ["at file level"]


class TestConsoleBehavior:
    def test_console_filter_drops_below_console_level(self, tmp_path):
        _configure(tmp_path, console_level=logging.INFO)
        processors = structlog.get_config()["processors"]
        console_filter = [
            p
            for p in processors
            if getattr(p, "_talemate_debuglog", False)
            and p.__name__ == "console_level_filter"
        ][0]

        with pytest.raises(structlog.DropEvent):
            console_filter(None, "debug", {"event": "x"})
        assert console_filter(None, "info", {"event": "x"}) == {"event": "x"}

    def test_console_renderer_still_terminal_processor(self, tmp_path):
        before = structlog.get_config()["processors"]
        assert isinstance(before[-1], structlog.dev.ConsoleRenderer)
        _configure(tmp_path)
        after = structlog.get_config()["processors"]
        assert isinstance(after[-1], structlog.dev.ConsoleRenderer)
        # the pre-existing default processors are preserved, in order
        assert after[: len(before) - 1] == before[:-1]


class TestConfiguration:
    def test_disabled_config_means_no_file_and_info_filtering(self, tmp_path):
        cfg = DebugLogConfig(enabled=False, path=str(tmp_path / "debug.jsonl"))
        debuglog.configure_structlog(console_level=logging.INFO, debug_log=cfg)

        structlog.get_logger("test").debug("dropped entirely")
        assert not (tmp_path / "debug.jsonl").exists()
        assert debuglog._file_handler is None

    def test_configure_is_idempotent(self, tmp_path):
        path = _configure(tmp_path)
        first = structlog.get_config()["processors"]
        # second call (e.g. reload-in-place) must not stack processors/handlers
        _configure(tmp_path)
        second = structlog.get_config()["processors"]
        assert second == first

        structlog.get_logger("test").debug("once")
        assert len(_read_lines(path)) == 1

    def test_broken_sink_degrades_to_console_only(self, tmp_path):
        marker = tmp_path / "not-a-dir"
        marker.write_text("file, not dir", encoding="utf-8")
        cfg = DebugLogConfig(path=str(marker / "sub" / "debug.jsonl"))

        # mkdir under a file raises; configure must swallow it
        debuglog.configure_structlog(console_level=logging.INFO, debug_log=cfg)
        assert debuglog._file_handler is None
        structlog.get_logger("test").info("still logs to console")

    def test_config_roundtrip(self):
        cfg = DebugLogConfig()
        assert cfg.enabled is True
        assert cfg.level == "DEBUG"
        restored = DebugLogConfig.model_validate(cfg.model_dump())
        assert restored == cfg
