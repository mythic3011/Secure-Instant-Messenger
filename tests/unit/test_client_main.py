from __future__ import annotations

import logging
import signal
from collections.abc import Callable
from typing import Any

import pytest

from client import main as client_main


class _FakeSignalExitApp:
    def __init__(self, *, run: Callable[[], None], exit: Callable[[], None] | None = None) -> None:
        self._run = run
        self._exit = exit or (lambda: None)
        self.exited = False

    def run(self) -> None:
        self._run()

    def exit(self) -> None:
        self.exited = True
        self._exit()


def test_run_app_with_sigint_exit_requests_clean_exit_and_restores_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous_handler = signal.default_int_handler
    installed_handlers: list[Any] = []
    restored_handlers: list[Any] = []
    log_messages: list[str] = []

    def _run() -> None:
        installed_handlers[0](signal.SIGINT, None)

    app = _FakeSignalExitApp(run=_run)

    monkeypatch.setattr(client_main.signal, "getsignal", lambda _sig: previous_handler)

    def _signal(sig: signal.Signals, handler: Any) -> None:
        if not installed_handlers:
            installed_handlers.append(handler)
            return
        restored_handlers.append(handler)

    monkeypatch.setattr(client_main.signal, "signal", _signal)
    logger = logging.getLogger("test.client.main")
    monkeypatch.setattr(logger, "info", lambda message, *_args: log_messages.append(message))

    client_main._run_app_with_sigint_exit(app, logger)

    assert app.exited is True
    assert restored_handlers == [previous_handler]
    assert log_messages == ["Client shut down by user (SIGINT/Ctrl+C)"]


def test_run_app_with_sigint_exit_falls_back_to_keyboardinterrupt_when_signal_install_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_messages: list[str] = []

    def _run() -> None:
        raise KeyboardInterrupt

    app = _FakeSignalExitApp(run=_run)

    def _signal(*_args: Any, **_kwargs: Any) -> None:
        raise ValueError("signal only works in main thread")

    monkeypatch.setattr(client_main.signal, "signal", _signal)
    logger = logging.getLogger("test.client.main.fallback")
    monkeypatch.setattr(logger, "info", lambda message, *_args: log_messages.append(message))

    client_main._run_app_with_sigint_exit(app, logger)

    assert log_messages == ["Client shut down by user (KeyboardInterrupt)"]


def test_run_app_with_sigint_exit_restores_handler_when_run_raises_keyboardinterrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous_handler = signal.default_int_handler
    installed_handlers: list[Any] = []
    restored_handlers: list[Any] = []
    log_messages: list[str] = []

    def _run() -> None:
        installed_handlers[0](signal.SIGINT, None)
        raise KeyboardInterrupt

    app = _FakeSignalExitApp(run=_run)

    monkeypatch.setattr(client_main.signal, "getsignal", lambda _sig: previous_handler)

    def _signal(sig: signal.Signals, handler: Any) -> None:
        if not installed_handlers:
            installed_handlers.append(handler)
            return
        restored_handlers.append(handler)

    monkeypatch.setattr(client_main.signal, "signal", _signal)
    logger = logging.getLogger("test.client.main.keyboardinterrupt")
    monkeypatch.setattr(logger, "info", lambda message, *_args: log_messages.append(message))

    client_main._run_app_with_sigint_exit(app, logger)

    assert app.exited is True
    assert restored_handlers == [previous_handler]
    assert log_messages == ["Client shut down by user (KeyboardInterrupt)"]
