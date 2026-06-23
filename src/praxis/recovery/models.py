"""Pydantic models for the auto-healing recovery agent."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class RecoveryPattern(StrEnum):
    """Root-cause patterns the diagnoser can identify."""
    EMPTY_MESSAGE_ID = "empty_message_id"
    TOOL_RETURNED_EMPTY = "tool_returned_empty"
    MODEL_EMITTED_TEXT_PROTOCOL = "model_emitted_text_protocol"
    WEBHOOK_SECRET_MISMATCH = "webhook_secret_mismatch"
    SERVICE_UNAVAILABLE = "service_unavailable"
    RATE_LIMITED = "rate_limited"
    HINDSIGHT_422 = "hindsight_422"
    UNKNOWN = "unknown"


class RecoveryActionType(StrEnum):
    """Recovery actions the worker can apply."""
    RETRY_WITH_ARGS = "retry_with_args"
    SWITCH_MODEL = "switch_model"
    RE_REGISTER_WEBHOOK = "re_register_webhook"
    REPLAY_EMAIL = "replay_email"
    BACKOFF_RETRY = "backoff_retry"
    ESCALATE_MANUAL = "escalate_manual"
    NONE = "none"


class FailureContext(BaseModel):
    """Context captured when a tool fails. Fed to the diagnoser."""
    tool_name: str
    error_message: str
    args: dict[str, Any] = Field(default_factory=dict)
    thread_id: str = ""
    event_id: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    traceback_str: str = ""
    model_name: str = ""
    raw_result_json: str = ""


class RecoveryEvent(BaseModel):
    """A single failure + its diagnosis + the action taken."""
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    failure: FailureContext
    pattern: RecoveryPattern = RecoveryPattern.UNKNOWN
    action_type: RecoveryActionType = RecoveryActionType.NONE
    action_details: dict[str, Any] = Field(default_factory=dict)
    retry_count: int = 0
    status: Literal["pending", "diagnosed", "recovering", "recovered", "failed", "escalated"] = "pending"
    result_message: str = ""
    result_success: bool | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    processed_at: datetime | None = None


class RecoveryStats(BaseModel):
    """Aggregate stats surfaced via /admin/recovery/stats."""
    total_failures: int = 0
    total_recovered: int = 0
    total_failed: int = 0
    total_escalated: int = 0
    by_pattern: dict[str, dict[str, int]] = Field(default_factory=dict)
    by_tool: dict[str, int] = Field(default_factory=dict)
    queue_size: int = 0
    worker_running: bool = False
