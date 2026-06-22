"""Tests for the migration safety guard (_migration_guard.confirm_apply).

These run without Firebase: the guard only touches stdin / env / stdout.
"""
import builtins
import os
import sys

import pytest

# Import the guard module from the back root (sibling of tests/).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _migration_guard  # noqa: E402


class _FakeStdin:
    def __init__(self, tty: bool):
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_non_interactive_without_env_aborts(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=False))
    monkeypatch.delenv("MIGRATION_CONFIRM", raising=False)
    with pytest.raises(SystemExit):
        _migration_guard.confirm_apply("users")


def test_non_interactive_with_matching_env_passes(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=False))
    monkeypatch.setenv("MIGRATION_CONFIRM", "users")
    _migration_guard.confirm_apply("users")  # no raise


def test_non_interactive_with_wrong_env_aborts(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=False))
    monkeypatch.setenv("MIGRATION_CONFIRM", "grupos")
    with pytest.raises(SystemExit):
        _migration_guard.confirm_apply("users")


def test_interactive_correct_token_passes(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr(builtins, "input", lambda _prompt="": "users")
    _migration_guard.confirm_apply("users")  # no raise


def test_interactive_wrong_token_aborts(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr(builtins, "input", lambda _prompt="": "nope")
    with pytest.raises(SystemExit):
        _migration_guard.confirm_apply("users")
