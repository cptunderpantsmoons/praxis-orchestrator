"""Smoke tests for recovery models."""


from praxis.recovery import (
    FailureContext,
    RecoveryActionType,
    RecoveryEvent,
    RecoveryPattern,
    RecoveryStats,
    get_recovery_manager,
    set_recovery_manager,
)


def test_failure_context_defaults():
    f = FailureContext(tool_name="reply_email", error_message="oops")
    assert f.tool_name == "reply_email"
    assert f.args == {}
    assert f.timestamp  # auto-set


def test_recovery_event_defaults():
    f = FailureContext(tool_name="list_agents", error_message="empty")
    e = RecoveryEvent(failure=f)
    assert e.event_id  # auto-generated
    assert e.pattern == RecoveryPattern.UNKNOWN
    assert e.action_type == RecoveryActionType.NONE
    assert e.status == "pending"
    assert e.retry_count == 0


def test_recovery_stats_defaults():
    s = RecoveryStats()
    assert s.total_failures == 0
    assert s.by_pattern == {}
    assert s.worker_running is False


def test_get_set_recovery_manager_singleton():
    assert get_recovery_manager() is None
    set_recovery_manager(object())  # type: ignore[arg-type]
    assert get_recovery_manager() is not None
    set_recovery_manager(None)
    assert get_recovery_manager() is None
