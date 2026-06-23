"""Root-cause diagnosis: pattern-matching rules for tool failures."""

from __future__ import annotations

from praxis.recovery.models import FailureContext, RecoveryActionType, RecoveryPattern

_PATTERN_TO_ACTION: dict[RecoveryPattern, RecoveryActionType] = {
    RecoveryPattern.EMPTY_MESSAGE_ID: RecoveryActionType.RETRY_WITH_ARGS,
    RecoveryPattern.TOOL_RETURNED_EMPTY: RecoveryActionType.RETRY_WITH_ARGS,
    RecoveryPattern.MODEL_EMITTED_TEXT_PROTOCOL: RecoveryActionType.SWITCH_MODEL,
    RecoveryPattern.WEBHOOK_SECRET_MISMATCH: RecoveryActionType.RE_REGISTER_WEBHOOK,
    RecoveryPattern.SERVICE_UNAVAILABLE: RecoveryActionType.BACKOFF_RETRY,
    RecoveryPattern.RATE_LIMITED: RecoveryActionType.BACKOFF_RETRY,
    RecoveryPattern.HINDSIGHT_422: RecoveryActionType.RETRY_WITH_ARGS,
    RecoveryPattern.UNKNOWN: RecoveryActionType.ESCALATE_MANUAL,
}


class Diagnoser:
    """Maps a FailureContext to a RecoveryPattern via priority-ordered rules.

    Rule priority rationale (first match wins):
    1. ``empty_message_id`` — specific to email tools, highest priority because
       it's the most common silent failure.
    2. ``tool_returned_empty`` — specific to list_agents/delegate, before
       generic service errors.
    3. ``model_text_protocol`` — specific to react_loop.
    4. ``webhook_secret_mismatch`` — specific to webhook ingest.
    5. ``hindsight_422`` — specific to hermes tools, before generic rate/service
       errors.
    6. ``rate_limited`` — generic, before service_unavailable (429 is more
       specific than 5xx).
    7. ``service_unavailable`` — most generic, last.
    """

    def __init__(self) -> None:
        self._rules = [
            self._rule_empty_message_id,
            self._rule_tool_returned_empty,
            self._rule_model_text_protocol,
            self._rule_webhook_secret_mismatch,
            self._rule_hindsight_422,
            self._rule_rate_limited,
            self._rule_service_unavailable,
        ]

    def diagnose(self, failure: FailureContext) -> RecoveryPattern:
        """Return the first matching pattern, or UNKNOWN."""
        for rule in self._rules:
            pattern = rule(failure)
            if pattern is not None:
                return pattern
        return RecoveryPattern.UNKNOWN

    def action_for(self, pattern: RecoveryPattern) -> RecoveryActionType:
        """Return the action type for a pattern (ESCALATE_MANUAL for UNKNOWN)."""
        return _PATTERN_TO_ACTION.get(pattern, RecoveryActionType.ESCALATE_MANUAL)

    # ── Rules (priority order — first match wins) ────────────────

    def _rule_empty_message_id(self, f: FailureContext) -> RecoveryPattern | None:
        if f.tool_name not in ("reply_email", "send_email"):
            return None
        err_lower = f.error_message.lower()
        if "message_id" in err_lower or "empty" in err_lower or "no message" in err_lower:
            return RecoveryPattern.EMPTY_MESSAGE_ID
        raw = f.raw_result_json
        if '"message_id": ""' in raw or '"message_id":""' in raw:
            if '"success": false' in raw or '"success":false' in raw:
                return RecoveryPattern.EMPTY_MESSAGE_ID
        return None

    def _rule_tool_returned_empty(self, f: FailureContext) -> RecoveryPattern | None:
        if f.tool_name == "list_agents":
            raw = f.raw_result_json
            if '"agents": []' in raw or '"agents":[]' in raw:
                if f.args.get("region"):
                    return RecoveryPattern.TOOL_RETURNED_EMPTY
        if f.tool_name == "delegate_to_agent":
            err_lower = f.error_message.lower()
            if "not found" in err_lower or "no agent" in err_lower:
                return RecoveryPattern.TOOL_RETURNED_EMPTY
        return None

    def _rule_model_text_protocol(self, f: FailureContext) -> RecoveryPattern | None:
        if f.tool_name != "react_loop":
            return None
        err_lower = f.error_message.lower()
        if "tool:" in err_lower or "text protocol" in err_lower or "legacy" in err_lower:
            return RecoveryPattern.MODEL_EMITTED_TEXT_PROTOCOL
        return None

    def _rule_webhook_secret_mismatch(self, f: FailureContext) -> RecoveryPattern | None:
        if f.tool_name != "webhook_email_ingest":
            return None
        err_lower = f.error_message.lower()
        if any(k in err_lower for k in ("signature", "svix", "unauthorized", "401")):
            return RecoveryPattern.WEBHOOK_SECRET_MISMATCH
        return None

    def _rule_hindsight_422(self, f: FailureContext) -> RecoveryPattern | None:
        if f.tool_name not in ("hermes_recall", "hermes_store", "hermes_learn", "hindsight_recall"):
            return None
        if "422" in f.error_message or "validation" in f.error_message.lower():
            return RecoveryPattern.HINDSIGHT_422
        return None

    def _rule_rate_limited(self, f: FailureContext) -> RecoveryPattern | None:
        err_lower = f.error_message.lower()
        if any(k in err_lower for k in ("429", "rate limit", "too many requests")):
            return RecoveryPattern.RATE_LIMITED
        return None

    def _rule_service_unavailable(self, f: FailureContext) -> RecoveryPattern | None:
        err_lower = f.error_message.lower()
        if any(k in err_lower for k in (
            "connection refused", "timeout", "timed out",
            "503", "502", "500", "service unavailable", "connecterror",
        )):
            return RecoveryPattern.SERVICE_UNAVAILABLE
        return None
