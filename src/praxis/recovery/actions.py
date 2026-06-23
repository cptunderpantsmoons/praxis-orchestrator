"""Recovery action implementations for the auto-healing agent.

Each action is an async method on ``ActionDispatcher`` that receives a
``RecoveryEvent`` and returns an ``ActionResult``. The dispatcher receives
``app.state`` at construction so actions can access services (hermes, LDR,
document, agent_delegator) and the FastAPI ``app`` (for replay_email).
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import orjson
import structlog

from praxis.recovery.models import RecoveryActionType, RecoveryEvent, RecoveryPattern

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = structlog.get_logger()


@dataclass
class ActionResult:
    """Outcome of a recovery action."""
    success: bool
    message: str


def _normalize_query(query: str) -> str:
    """Normalize a hindsight query — strip special chars, collapse whitespace."""
    cleaned = re.sub(r"[^\w\s]", " ", query)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip().lower()


class ActionDispatcher:
    """Dispatches recovery actions by type. Receives app.state for service access."""

    def __init__(self, app: "FastAPI | None" = None) -> None:
        self._app = app

    async def dispatch(self, event: RecoveryEvent) -> ActionResult:
        handler = {
            RecoveryActionType.RETRY_WITH_ARGS: self._retry_with_args,
            RecoveryActionType.SWITCH_MODEL: self._switch_model,
            RecoveryActionType.RE_REGISTER_WEBHOOK: self._re_register_webhook,
            RecoveryActionType.REPLAY_EMAIL: self._replay_email,
            RecoveryActionType.BACKOFF_RETRY: self._backoff_retry,
            RecoveryActionType.ESCALATE_MANUAL: self._escalate,
            RecoveryActionType.NONE: self._noop,
        }.get(event.action_type, self._escalate)
        return await handler(event)

    def _get_services(self) -> dict[str, Any]:
        """Extract services from app.state. Returns dict with None for missing."""
        if self._app is None:
            return {
                "hermes_service": None,
                "ldr_service": None,
                "document_service": None,
                "agent_delegator": None,
            }
        state = self._app.state
        return {
            "hermes_service": getattr(state, "hermes_service", None),
            "ldr_service": getattr(state, "ldr_service", None),
            "document_service": getattr(state, "document_service", None),
            "agent_delegator": getattr(state, "agent_delegator", None),
        }

    # ── Actions ──────────────────────────────────────────────────

    async def _retry_with_args(self, event: RecoveryEvent) -> ActionResult:
        """Strip the offending arg and re-invoke the tool."""
        f = event.failure
        corrected = dict(f.args)
        # Put the name back (it was popped by _execute_tool_inner)
        if "name" not in corrected:
            corrected["name"] = f.tool_name
        action_detail: dict[str, Any] = {}

        if f.tool_name == "list_agents" and event.pattern == RecoveryPattern.TOOL_RETURNED_EMPTY:
            corrected.pop("region", None)
            action_detail["removed"] = "region"
        elif f.tool_name in ("reply_email", "send_email") and event.pattern == RecoveryPattern.EMPTY_MESSAGE_ID:
            known_id = event.action_details.get("known_message_id", "")
            if known_id:
                corrected["message_id"] = known_id
                action_detail["set"] = "message_id"
        elif event.pattern == RecoveryPattern.HINDSIGHT_422:
            if "query" in corrected:
                corrected["query"] = _normalize_query(corrected["query"])
                action_detail["normalized"] = "query"

        from praxis.graph.nodes import _execute_tool_inner
        services = self._get_services()
        try:
            name, result = await _execute_tool_inner(
                corrected,
                hermes_service=services["hermes_service"],
                ldr_service=services["ldr_service"],
                document_service=services["document_service"],
                agent_delegator=services["agent_delegator"],
            )
            success = getattr(result, "success", False)
            msg = getattr(result, "message", "") or str(result)[:200]
            event.action_details.update(action_detail)
            return ActionResult(
                success=success,
                message=f"Retried {f.tool_name} with corrected args ({action_detail}). Result: {msg[:200]}",
            )
        except Exception as exc:
            return ActionResult(success=False, message=f"Retry failed: {exc}")

    async def _switch_model(self, event: RecoveryEvent) -> ActionResult:
        """Toggle _MODEL_OVERRIDE between umans-coder and umans-flash, then replay."""
        from praxis.graph import nodes as graph_nodes
        current = graph_nodes._MODEL_OVERRIDE
        if current == "umans-coder":
            new_model = "umans-flash"
        elif current == "umans-flash":
            new_model = "umans-coder"
        else:
            # Default: switch to umans-coder (the reliable native tool-caller)
            new_model = "umans-coder"
        graph_nodes._MODEL_OVERRIDE = new_model
        logger.info("recovery.switch_model", new_model=new_model, previous=current)

        # Replay the failed email if we have an event_id
        if event.failure.event_id:
            replay_result = await self._replay_email(event)
            return ActionResult(
                success=replay_result.success,
                message=f"Switched model to {new_model}; replay: {replay_result.message}",
            )
        return ActionResult(success=True, message=f"Switched ReAct model to {new_model}")

    async def _re_register_webhook(self, event: RecoveryEvent) -> ActionResult:
        """Re-register the AgentMail webhook and rotate the secret."""
        from praxis.config import get_settings, reset_settings
        from praxis.services.agentmail_v2 import get_agentmail_v2
        from praxis.webhooks.settings_admin import (
            _env_file_path,
            _read_env_as_dict,
            _write_env_dict,
        )

        settings = get_settings()
        webhook_url = f"{settings.praxis_api_url}/webhook/email"
        client = get_agentmail_v2()

        try:
            # 1. List existing webhooks, delete any pointing at our URL
            existing = await client.list_webhooks()
            deleted_ids: list[str] = []
            for wh in existing:
                wh_url = getattr(wh, "url", None)
                if wh_url is None and isinstance(wh, dict):
                    wh_url = wh.get("url", "")
                if wh_url and webhook_url in str(wh_url):
                    wh_id = getattr(wh, "id", None)
                    if wh_id is None and isinstance(wh, dict):
                        wh_id = wh.get("id", "")
                    if wh_id:
                        await client.delete_webhook(str(wh_id))
                        deleted_ids.append(str(wh_id))

            # 2. Create new webhook
            new_wh = await client.create_webhook(
                url=webhook_url,
                events=["message.received", "message.sent", "message.delivered"],
                enabled=True,
            )
            new_wh_id = getattr(new_wh, "id", None)
            if new_wh_id is None and isinstance(new_wh, dict):
                new_wh_id = new_wh.get("id", "")

            # 3. Rotate the secret to get a fresh one
            new_secret = ""
            if new_wh_id:
                try:
                    rotated = await client.rotate_webhook_secret(str(new_wh_id))
                    new_secret = getattr(rotated, "secret", "") or (
                        rotated.get("secret", "") if isinstance(rotated, dict) else ""
                    )
                except Exception as exc:
                    logger.warning("recovery.rotate_secret_failed", error=str(exc))

            # 4. Update .env with the new secret
            if new_secret:
                try:
                    env_path = _env_file_path()
                    existing_env = _read_env_as_dict(env_path)
                    existing_env["AGENTMAIL_WEBHOOK_SECRET"] = new_secret
                    _write_env_dict(env_path, existing_env)
                    reset_settings()
                except Exception as exc:
                    return ActionResult(
                        success=False,
                        message=f"Webhook re-registered (id={new_wh_id}) but .env update failed: {exc}",
                    )

            return ActionResult(
                success=True,
                message=f"Re-registered webhook at {webhook_url}, deleted {len(deleted_ids)} old, new id={new_wh_id}, secret_rotated={bool(new_secret)}",
            )
        except Exception as exc:
            return ActionResult(success=False, message=f"Webhook re-registration failed: {exc}")

    async def _replay_email(self, event: RecoveryEvent) -> ActionResult:
        """Look up the failed email in the dead-letter log and replay it."""
        from praxis.config import get_settings
        from praxis.webhooks.admin import _mark_event_resolved, _read_failed_events
        from praxis.webhooks.email import (
            _failed_events_path,
            _parse_inbound_email,
            _process_received_email,
        )
        from praxis.webhooks.payloads import parse_agentmail_event

        if self._app is None:
            return ActionResult(success=False, message="No app context for replay")

        settings = get_settings()
        target_event_id = event.failure.event_id
        if not target_event_id:
            return ActionResult(success=False, message="No event_id to replay")

        events = _read_failed_events(settings)
        target = None
        for e in events:
            if str(e.get("event_id", "")) == target_event_id:
                target = e
                break
        if target is None:
            return ActionResult(
                success=False,
                message=f"Failed event {target_event_id} not found in dead-letter log",
            )

        raw_payload = target.get("payload", "")
        if not raw_payload:
            return ActionResult(success=False, message="Failed event has no stored payload")

        raw_body = raw_payload.encode("utf-8") if isinstance(raw_payload, str) else raw_payload
        try:
            payload = orjson.loads(raw_body)
        except Exception as exc:
            return ActionResult(success=False, message=f"Failed to parse stored payload: {exc}")

        parsed = parse_agentmail_event(payload)
        if parsed is not None and parsed.is_received():
            email = parsed.to_inbound_email()
            if email is None:
                return ActionResult(success=False, message="Parsed event has no email payload")
            event_id = target_event_id
            thread_id = f"thread_{parsed.thread_id}" if parsed.thread_id else f"thread_{event_id}"
        else:
            email = _parse_inbound_email(payload)
            event_id = target_event_id
            thread_id = f"thread_{event_id}"

        try:
            result = await _process_received_email(
                app=self._app,
                email=email,
                event_id=event_id,
                thread_id=thread_id,
                settings=settings,
                raw_body=raw_body,
                is_retry=True,
            )
            if result.status == "accepted_retry":
                _mark_event_resolved(_failed_events_path(settings), event_id)
                return ActionResult(
                    success=True,
                    message=f"Replayed email {event_id}: {result.message}",
                )
            return ActionResult(
                success=False,
                message=f"Replay of {event_id} returned status {result.status}: {result.message}",
            )
        except Exception as exc:
            return ActionResult(success=False, message=f"Replay crashed: {exc}")

    async def _backoff_retry(self, event: RecoveryEvent) -> ActionResult:
        """Exponential backoff, then re-invoke the tool."""
        retry = event.retry_count
        delay = min(2 ** retry, 60)
        logger.info("recovery.backoff", event_id=event.event_id, retry=retry, delay=delay)
        await asyncio.sleep(delay)

        from praxis.graph.nodes import _execute_tool_inner
        f = event.failure
        corrected = dict(f.args)
        if "name" not in corrected:
            corrected["name"] = f.tool_name
        services = self._get_services()
        try:
            name, result = await _execute_tool_inner(
                corrected,
                hermes_service=services["hermes_service"],
                ldr_service=services["ldr_service"],
                document_service=services["document_service"],
                agent_delegator=services["agent_delegator"],
            )
            success = getattr(result, "success", False)
            msg = getattr(result, "message", "") or str(result)[:200]
            return ActionResult(
                success=success,
                message=f"Backoff retry after {delay}s: {msg[:200]}",
            )
        except Exception as exc:
            return ActionResult(success=False, message=f"Backoff retry failed: {exc}")

    async def _escalate(self, event: RecoveryEvent) -> ActionResult:
        """Escalate to manual intervention. No action attempted."""
        logger.warning(
            "recovery.escalated",
            event_id=event.event_id,
            tool_name=event.failure.tool_name,
            pattern=event.pattern,
            error=event.failure.error_message[:500],
        )
        return ActionResult(
            success=False,
            message=f"Escalated to manual: unknown failure pattern for {event.failure.tool_name}",
        )

    async def _noop(self, event: RecoveryEvent) -> ActionResult:
        """No action — for NONE action type."""
        return ActionResult(success=True, message="No action taken (action_type=NONE)")
