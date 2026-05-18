"""Tests for paper_server logger plumbing.

Targets the P3 #7 fix: dashboard_server.log was 180 bytes for 80h
of uptime because run_server used bare `print` which Task Scheduler
discards. We now route the startup banner and per-request log lines
through an injected logger when one is provided. These tests pin
both branches.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from detect_temperature.paper_server import _emit, _make_handler


def _capture_logger() -> tuple[logging.Logger, list[str]]:
    """Return a logger that buffers messages into the returned list.

    Important: we use a UNIQUE name per test (via id(list)) so concurrent
    pytest workers don't share handlers.
    """
    captured: list[str] = []
    logger = logging.getLogger(f"paper-server-test-{id(captured)}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for h in list(logger.handlers):
        logger.removeHandler(h)

    class ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record.getMessage())

    logger.addHandler(ListHandler())
    return logger, captured


def test_emit_uses_logger_when_provided(capsys) -> None:
    logger, captured = _capture_logger()
    _emit(logger, "hello world")

    assert captured == ["hello world"]
    # Must NOT also write to stdout when a logger is provided
    out = capsys.readouterr().out
    assert "hello world" not in out


def test_emit_falls_back_to_stdout_when_logger_is_none(capsys) -> None:
    _emit(None, "hello world")
    out = capsys.readouterr().out
    assert "hello world" in out


def test_handler_log_message_routes_to_logger(tmp_path: Path) -> None:
    """When the handler is built with a logger, every per-request
    log_message call must hit the logger instead of stdout. We can't
    easily spin up a real HTTP server in a unit test, so we
    instead synthesise a handler instance and call log_message
    directly the way BaseHTTPRequestHandler would."""
    logger, captured = _capture_logger()
    handler_cls = _make_handler(
        project_root=tmp_path,
        bankroll_usdc=100.0,
        finalization_lag_days=1,
        logger=logger,
    )

    # BaseHTTPRequestHandler normally needs request/client_address args,
    # but log_message only reads self.address_string. Bypass __init__
    # entirely and stub address_string to a fixed value.
    instance = handler_cls.__new__(handler_cls)
    instance.address_string = lambda: "192.0.2.1"

    instance.log_message("%s %s", "GET", "/dashboard")

    assert captured == ["192.0.2.1 - GET /dashboard"]


def test_handler_log_message_falls_back_to_stdout_without_logger(
    tmp_path: Path, capsys
) -> None:
    handler_cls = _make_handler(
        project_root=tmp_path,
        bankroll_usdc=100.0,
        finalization_lag_days=1,
        # logger= omitted -> default None
    )
    instance = handler_cls.__new__(handler_cls)
    instance.address_string = lambda: "203.0.113.7"

    instance.log_message("%s %s", "POST", "/api/refresh-paper")

    out = capsys.readouterr().out
    assert "203.0.113.7 - POST /api/refresh-paper" in out
