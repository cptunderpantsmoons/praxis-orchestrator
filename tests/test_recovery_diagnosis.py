"""Tests for the Diagnoser pattern-matching rules."""

from datetime import UTC, datetime

from praxis.recovery.diagnosis import Diagnoser
from praxis.recovery.models import FailureContext, RecoveryActionType, RecoveryPattern


def _ctx(
    tool_name: str = "dummy_search",
    error_message: str = "fail",
    args: dict | None = None,
    raw_result_json: str = "",
) -> FailureContext:
    return FailureContext(
        tool_name=tool_name,
        error_message=error_message,
        args=args or {},
        timestamp=datetime.now(UTC),
        raw_result_json=raw_result_json,
    )


class TestDiagnoser:
    def setup_method(self):
        self.d = Diagnoser()

    def test_empty_message_id_via_error_text(self):
        f = _ctx("reply_email", "send failed: no message_id")
        assert self.d.diagnose(f) == RecoveryPattern.EMPTY_MESSAGE_ID

    def test_empty_message_id_via_raw_json(self):
        f = _ctx("send_email", "fail", raw_result_json='{"success": false, "message_id": ""}')
        assert self.d.diagnose(f) == RecoveryPattern.EMPTY_MESSAGE_ID

    def test_empty_message_id_only_for_email_tools(self):
        f = _ctx("list_agents", "message_id missing")
        # Should not match empty_message_id (wrong tool), falls through
        assert self.d.diagnose(f) == RecoveryPattern.UNKNOWN

    def test_list_agents_empty_with_region(self):
        f = _ctx(
            "list_agents",
            "no agents",
            args={"region": "Australia"},
            raw_result_json='{"agents": [], "success": true}',
        )
        assert self.d.diagnose(f) == RecoveryPattern.TOOL_RETURNED_EMPTY

    def test_list_agents_empty_without_region_is_unknown(self):
        # Empty result without a region filter is a different bug — escalate
        f = _ctx(
            "list_agents",
            "no agents",
            args={},
            raw_result_json='{"agents": [], "success": true}',
        )
        assert self.d.diagnose(f) == RecoveryPattern.UNKNOWN

    def test_delegate_to_agent_not_found(self):
        f = _ctx("delegate_to_agent", "agent not found")
        assert self.d.diagnose(f) == RecoveryPattern.TOOL_RETURNED_EMPTY

    def test_model_text_protocol(self):
        f = _ctx("react_loop", "model emitted TOOL:search_inbox text")
        assert self.d.diagnose(f) == RecoveryPattern.MODEL_EMITTED_TEXT_PROTOCOL

    def test_webhook_secret_mismatch(self):
        f = _ctx("webhook_email_ingest", "Invalid Svix signature (401)")
        assert self.d.diagnose(f) == RecoveryPattern.WEBHOOK_SECRET_MISMATCH

    def test_hindsight_422(self):
        f = _ctx("hermes_recall", "Hindsight API returned 422 validation error")
        assert self.d.diagnose(f) == RecoveryPattern.HINDSIGHT_422

    def test_rate_limited(self):
        f = _ctx("hermes_recall", "429 Too Many Requests")
        assert self.d.diagnose(f) == RecoveryPattern.RATE_LIMITED

    def test_service_unavailable(self):
        f = _ctx("hermes_recall", "Connection refused to 127.0.0.1:8787")
        assert self.d.diagnose(f) == RecoveryPattern.SERVICE_UNAVAILABLE

    def test_hindsight_422_beats_rate_limited(self):
        # A hindsight 422 error that also contains "429" in some other text
        # should still match hindsight_422 (higher priority rule)
        f = _ctx("hermes_store", "422 validation failed")
        assert self.d.diagnose(f) == RecoveryPattern.HINDSIGHT_422

    def test_unknown_falls_through(self):
        f = _ctx("dummy_search", "weird mystery error")
        assert self.d.diagnose(f) == RecoveryPattern.UNKNOWN


class TestActionMapping:
    def setup_method(self):
        self.d = Diagnoser()

    def test_empty_message_id_maps_to_retry_with_args(self):
        assert self.d.action_for(RecoveryPattern.EMPTY_MESSAGE_ID) == RecoveryActionType.RETRY_WITH_ARGS

    def test_tool_returned_empty_maps_to_retry_with_args(self):
        assert self.d.action_for(RecoveryPattern.TOOL_RETURNED_EMPTY) == RecoveryActionType.RETRY_WITH_ARGS

    def test_model_text_protocol_maps_to_switch_model(self):
        assert self.d.action_for(RecoveryPattern.MODEL_EMITTED_TEXT_PROTOCOL) == RecoveryActionType.SWITCH_MODEL

    def test_webhook_secret_mismatch_maps_to_re_register(self):
        assert self.d.action_for(RecoveryPattern.WEBHOOK_SECRET_MISMATCH) == RecoveryActionType.RE_REGISTER_WEBHOOK

    def test_service_unavailable_maps_to_backoff(self):
        assert self.d.action_for(RecoveryPattern.SERVICE_UNAVAILABLE) == RecoveryActionType.BACKOFF_RETRY

    def test_rate_limited_maps_to_backoff(self):
        assert self.d.action_for(RecoveryPattern.RATE_LIMITED) == RecoveryActionType.BACKOFF_RETRY

    def test_hindsight_422_maps_to_retry_with_args(self):
        assert self.d.action_for(RecoveryPattern.HINDSIGHT_422) == RecoveryActionType.RETRY_WITH_ARGS

    def test_unknown_maps_to_escalate(self):
        assert self.d.action_for(RecoveryPattern.UNKNOWN) == RecoveryActionType.ESCALATE_MANUAL
