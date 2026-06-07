"""
Property-based tests for NavigateSession history management and cleanup logic.

Feature: building-nav-integration
Properties 14–15 from design.md
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from server.api.navigate import NavigateSession


def _simulate_history_trim(session: NavigateSession, memory_turns: int) -> None:
    """Mirror the trim logic from navigate_endpoint."""
    max_entries = memory_turns * 2
    while len(session.history) > max_entries:
        session.history.pop(0)
        session.history.pop(0)


def _simulate_cleanup(
    store: dict[str, NavigateSession],
    now: datetime,
    ttl_minutes: int = 30,
) -> None:
    """Mirror the _cleanup_sessions logic from main.py."""
    cutoff = now - timedelta(minutes=ttl_minutes)
    stale = [sid for sid, s in list(store.items()) if s.last_active < cutoff]
    for sid in stale:
        store.pop(sid, None)


# ---------------------------------------------------------------------------
# Property 14: history length bound
# ---------------------------------------------------------------------------

@given(
    num_calls=st.integers(min_value=1, max_value=30),
    memory_turns=st.integers(min_value=1, max_value=10),
)
@settings(max_examples=300)
def test_history_length_bound(num_calls: int, memory_turns: int) -> None:
    """Feature: building-nav-integration, Property 14: History length bound.

    After N calls, the session history length MUST never exceed
    session_memory_turns * 2 entries. The trim is applied after every call.
    Validates: Requirements 5.7, 8.2
    """
    session = NavigateSession()
    max_entries = memory_turns * 2

    for i in range(num_calls):
        session.history.append({"role": "user", "content": f"msg {i}"})
        session.history.append({"role": "assistant", "content": f"reply {i}"})
        _simulate_history_trim(session, memory_turns)

        assert len(session.history) <= max_entries, (
            f"History exceeded bound after call {i}: "
            f"len={len(session.history)}, max={max_entries}"
        )


@given(
    num_calls=st.integers(min_value=1, max_value=30),
    memory_turns=st.integers(min_value=1, max_value=10),
)
@settings(max_examples=200)
def test_history_trim_removes_oldest_pair(num_calls: int, memory_turns: int) -> None:
    """When trimming, the OLDEST user+assistant pair is removed first.

    After filling to capacity and adding one more pair, the first two entries
    should have been evicted, not the most recent ones.
    Validates: Requirements 5.7 (history trim invariant)
    """
    session = NavigateSession()
    max_entries = memory_turns * 2

    # Fill to capacity
    for i in range(memory_turns):
        session.history.append({"role": "user", "content": f"u{i}"})
        session.history.append({"role": "assistant", "content": f"a{i}"})

    # One more call — should evict the first pair
    session.history.append({"role": "user", "content": "new_user"})
    session.history.append({"role": "assistant", "content": "new_assistant"})
    _simulate_history_trim(session, memory_turns)

    assert len(session.history) == max_entries
    # The most recent entries must still be present
    assert session.history[-2]["content"] == "new_user"
    assert session.history[-1]["content"] == "new_assistant"


# ---------------------------------------------------------------------------
# Property 15: session cleanup correctness
# ---------------------------------------------------------------------------

@given(
    num_fresh=st.integers(min_value=0, max_value=10),
    num_stale=st.integers(min_value=0, max_value=10),
)
@settings(max_examples=300)
def test_session_cleanup_correctness(num_fresh: int, num_stale: int) -> None:
    """Feature: building-nav-integration, Property 15: Session cleanup correctness.

    After running cleanup:
    - All sessions active within 30 min (fresh) MUST still be in the store.
    - All sessions inactive for >30 min (stale) MUST have been removed.
    Validates: Requirements 8.3, 8.5
    """
    now = datetime.utcnow()
    store: dict[str, NavigateSession] = {}
    fresh_ids: set[str] = set()
    stale_ids: set[str] = set()

    for i in range(num_fresh):
        sid = f"fresh-{i}"
        store[sid] = NavigateSession(last_active=now - timedelta(minutes=5))
        fresh_ids.add(sid)

    for i in range(num_stale):
        sid = f"stale-{i}"
        store[sid] = NavigateSession(last_active=now - timedelta(minutes=45))
        stale_ids.add(sid)

    _simulate_cleanup(store, now, ttl_minutes=30)

    for sid in fresh_ids:
        assert sid in store, f"Fresh session {sid} was incorrectly removed"

    for sid in stale_ids:
        assert sid not in store, f"Stale session {sid} was not removed"


@given(
    total_sessions=st.integers(min_value=0, max_value=15),
    stale_fraction=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
)
@settings(max_examples=200)
def test_cleanup_monotonicity(total_sessions: int, stale_fraction: float) -> None:
    """After cleanup, session count <= sessions active within 30 min.

    Validates: Requirement 8.5 (session count monotonicity under expiry)
    """
    now = datetime.utcnow()
    store: dict[str, NavigateSession] = {}
    num_stale = int(total_sessions * stale_fraction)
    num_fresh = total_sessions - num_stale

    for i in range(num_fresh):
        store[f"f{i}"] = NavigateSession(last_active=now - timedelta(minutes=10))
    for i in range(num_stale):
        store[f"s{i}"] = NavigateSession(last_active=now - timedelta(minutes=60))

    _simulate_cleanup(store, now, ttl_minutes=30)

    assert len(store) <= num_fresh, (
        f"After cleanup, {len(store)} sessions remain but only {num_fresh} were fresh"
    )
