"""Session state machine — the single source of truth for booth UI state.

Server-side per photobooth-plan.md §5 principle 3: the UI is driven by the
WebSocket event stream that mirrors transitions here, never by polling.
"""

from __future__ import annotations

from enum import StrEnum


class SessionState(StrEnum):
    IDLE = "idle"
    ARMED = "armed"
    COUNTDOWN = "countdown"
    CAPTURING = "capturing"
    REVIEW = "review"
    PROCESSING = "processing"


# Legal transitions. A transition not listed here is a bug, not a guest action.
ALLOWED_TRANSITIONS: dict[SessionState, frozenset[SessionState]] = {
    SessionState.IDLE: frozenset({SessionState.ARMED}),
    SessionState.ARMED: frozenset({SessionState.COUNTDOWN, SessionState.IDLE}),
    SessionState.COUNTDOWN: frozenset({SessionState.CAPTURING, SessionState.IDLE}),
    # IDLE covers a failed capture (camera error mid-flow) — REVIEW is the
    # success path only, a guest never "reviews" a failure.
    # COUNTDOWN: collage mode (T-2.6) shoots N slots back-to-back — a
    # successful shot that isn't the template's last slot loops back to
    # COUNTDOWN for the next one instead of going straight to REVIEW.
    SessionState.CAPTURING: frozenset(
        {SessionState.REVIEW, SessionState.IDLE, SessionState.COUNTDOWN}
    ),
    SessionState.REVIEW: frozenset({SessionState.PROCESSING, SessionState.IDLE}),
    SessionState.PROCESSING: frozenset({SessionState.IDLE}),
}


class InvalidTransitionError(Exception):
    def __init__(self, current: SessionState, target: SessionState) -> None:
        super().__init__(f"cannot transition {current} -> {target}")
        self.current = current
        self.target = target


class SessionStateMachine:
    """In-memory state for one guest session. Persistence is a storage concern."""

    def __init__(self, initial: SessionState = SessionState.IDLE) -> None:
        self._state = initial

    @property
    def state(self) -> SessionState:
        return self._state

    def transition(self, target: SessionState) -> None:
        if target not in ALLOWED_TRANSITIONS[self._state]:
            raise InvalidTransitionError(self._state, target)
        self._state = target
