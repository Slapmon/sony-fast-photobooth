from __future__ import annotations

import pytest

from photobooth.core.state import InvalidTransitionError, SessionState, SessionStateMachine


def test_starts_idle() -> None:
    sm = SessionStateMachine()
    assert sm.state == SessionState.IDLE


def test_walks_the_happy_path() -> None:
    sm = SessionStateMachine()
    for target in (
        SessionState.ARMED,
        SessionState.COUNTDOWN,
        SessionState.CAPTURING,
        SessionState.REVIEW,
        SessionState.PROCESSING,
        SessionState.IDLE,
    ):
        sm.transition(target)
    assert sm.state == SessionState.IDLE


def test_rejects_illegal_transition() -> None:
    sm = SessionStateMachine()
    with pytest.raises(InvalidTransitionError):
        sm.transition(SessionState.CAPTURING)
