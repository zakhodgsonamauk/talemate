"""
Structured JSONL debug file sink for structlog.

Talemate's structlog setup only installs a level-filtering wrapper class; the
processor chain is structlog's default (ending in ConsoleRenderer) and never
touches stdlib logging. That wrapper drops debug events before any processor
runs, which is why agent decision events (`choose_subject`,
`attach_character_references.*`, ...) have been invisible.

This module keeps the console pipeline byte-for-byte identical and adds a
rotating JSONL file that captures DEBUG and up:

- the wrapper filter is lowered so debug events reach the processors at all,
- a tee processor serializes every event dict to the file,
- a console filter processor reproduces the level cut the wrapper used to do,
  so console output does not change.

Never raises out of configuration or logging: a broken sink degrades to
console-only behavior, it must not take the server down.
"""

import json
import logging
import logging.handlers
from pathlib import Path

import structlog

from talemate.path import TALEMATE_ROOT

__all__ = ["configure_structlog"]

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "exception": logging.ERROR,
    "critical": logging.CRITICAL,
    "fatal": logging.CRITICAL,
}

_file_handler: logging.Handler | None = None
_configured: bool = False


def _level_no(method_name: str) -> int:
    return _LEVELS.get(method_name, logging.INFO)


def _make_tee(handler: logging.Handler):
    def tee_to_jsonl(logger, method_name, event_dict):
        levelno = _level_no(method_name)
        if levelno >= handler.level:
            try:
                payload = event_dict
                if "exc_info" in event_dict:
                    # Render the traceback into a string for the file; operate on
                    # a copy so the console renderer still sees the raw exc_info.
                    payload = structlog.processors.format_exc_info(
                        logger, method_name, dict(event_dict)
                    )
                line = json.dumps(payload, default=str, ensure_ascii=False)
            except Exception:
                try:
                    line = json.dumps(
                        {
                            "event": "unserializable log event",
                            "repr": repr(event_dict)[:2000],
                        }
                    )
                except Exception:
                    return event_dict
            try:
                record = logging.LogRecord(
                    "talemate", levelno, "", 0, line, None, None
                )
                handler.handle(record)
            except Exception:
                pass
        return event_dict

    tee_to_jsonl._talemate_debuglog = True
    return tee_to_jsonl


def _make_console_filter(console_level: int):
    def console_level_filter(logger, method_name, event_dict):
        if _level_no(method_name) < console_level:
            raise structlog.DropEvent
        return event_dict

    console_level_filter._talemate_debuglog = True
    return console_level_filter


def configure_structlog(console_level: int, debug_log=None) -> None:
    """
    (Re)configure structlog with the optional JSONL file sink.

    :param console_level: stdlib level number for console output — preserves
        the pre-existing TALEMATE_DEBUG behavior.
    :param debug_log: the ``config.debug_log`` block (or None / disabled for
        console-only behavior, identical to the historical setup).

    Idempotent: repeat calls are no-ops so a reload-in-place cannot stack
    duplicate file handlers or tee processors.
    """
    global _configured, _file_handler

    if _configured:
        return

    if debug_log is None or not debug_log.enabled:
        structlog.configure(
            wrapper_class=structlog.make_filtering_bound_logger(console_level),
        )
        _configured = True
        return

    try:
        path = Path(debug_log.path)
        if not path.is_absolute():
            path = TALEMATE_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)

        file_level = getattr(logging, str(debug_log.level).upper(), logging.DEBUG)
        if not isinstance(file_level, int):
            file_level = logging.DEBUG

        _file_handler = logging.handlers.RotatingFileHandler(
            path,
            maxBytes=debug_log.max_mb * 1024 * 1024,
            backupCount=debug_log.backups,
            encoding="utf-8",
        )
        _file_handler.setLevel(file_level)
    except Exception:
        structlog.configure(
            wrapper_class=structlog.make_filtering_bound_logger(console_level),
        )
        _configured = True
        return

    # Current processors are structlog's defaults (nothing else configures
    # them); strip any processors this module added previously so the splice
    # is safe even if the idempotence flag is bypassed.
    current = [
        p
        for p in structlog.get_config()["processors"]
        if not getattr(p, "_talemate_debuglog", False)
    ]

    processors = (
        current[:-1]
        + [_make_tee(_file_handler), _make_console_filter(console_level)]
        + [current[-1]]
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            min(console_level, file_level)
        ),
    )
    _configured = True


def _reset_for_tests() -> None:
    """Undo configuration and release the file handler (test helper)."""
    global _configured, _file_handler
    if _file_handler is not None:
        try:
            _file_handler.close()
        except Exception:
            pass
    _file_handler = None
    _configured = False
    structlog.reset_defaults()
