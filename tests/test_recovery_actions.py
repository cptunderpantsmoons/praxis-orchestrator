"""Tests for recovery actions."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from praxis.recovery.actions import ActionDispatcher, ActionResult, _normalize_query
from praxis.recovery.models import (
    FailureContext,
    RecoveryActionType,
    RecoveryEvent,
    RecoveryPattern,
)


def _event(
    tool_name: str = "dummy_search",
    error: str = "fail",
    pattern: RecoveryPattern = RecoveryPattern.UNKNOWN,
    action_type: RecoveryActionType = RecoveryActionType.NONE,
    args: dict | None = None,
    event_id: str = "",
) -> RecoveryEvent:
    return RecoveryEvent(
        failure=FailureContext(
            tool_name=tool_name,
            error_message=error,
            args=args or {},
            event_id=event_id,
            timestamp=datetime.now(UTC),
        ),
        pattern=pattern,
        action_type=action_type,
    )


class TestNormalizeQuery:
    def test_strips_special_chars(self):
        assert _normalize_query("hello! @world#") == "hello world"

    def test_collapses_whitespace(self):
        assert _normalize_query("hello   world\t\n") == "hello world"

    def test_lowercases(self):
        assert _normalize_query("HELLO World") == "hello world"

    def test_empty(self):
        assert _normalize_query("") == ""


class TestDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_routes_to_correct_handler(self):
        d = ActionDispatcher(app=None)
        # Each action type should route without crashing
        event = _event(action_type=RecoveryActionType.ESCALATE_MANUAL)
        result = await d.dispatch(event)
        assert result.success is False
        assert "Escalated" in result.message

    @pytest.mark.asyncio
    async def test_dispatch_none_action(self):
        d = ActionDispatcher(app=None)
        event = _event(action_type=RecoveryActionType.NONE)
        result = await d.dispatch(event)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_dispatch_unknown_action_escalates(self):
        d = ActionDispatcher(app=None)
        # Pass an invalid action_type — should fall back to escalate
        event = _event(action_type=RecoveryActionType.NONE)
        event.action_type = "bogus_action"  # type: ignore[assignment]
        result = await d.dispatch(event)
        assert result.success is False


class TestRetryWithArgs:
    @pytest.mark.asyncio
    async def test_list_agents_strips_region(self, monkeypatch):
        """retry_with_args on list_agents with TOOL_RETURNED_EMPTY strips region."""
        from praxis.models.schemas import AgentListingResult

        captured_calls: list[dict] = []

        async def mock_inner(tool_call, **kwargs):
            captured_calls.append(dict(tool_call))
            return "list_agents", AgentListingResult(
                division=tool_call.get("division", ""),
                keyword=tool_call.get("keyword", ""),
                region=tool_call.get("region", ""),
                agents=[{"slug": "test-agent"}],
                success=True,
            )
        monkeypatch.setattr("praxis.graph.nodes._execute_tool_inner", mock_inner)

        d = ActionDispatcher(app=None)
        event = _event(
            tool_name="list_agents",
            error="empty result",
            pattern=RecoveryPattern.TOOL_RETURNED_EMPTY,
            action_type=RecoveryActionType.RETRY_WITH_ARGS,
            args={"division": "engineering", "region": "Australia"},
        )
        result = await d.dispatch(event)
        assert result.success is True
        # Verify region was stripped before the call
        assert "region" not in captured_calls[0]
        assert captured_calls[0]["division"] == "engineering"
        # Verify the tool name was re-added (it was popped by _execute_tool_inner)
        assert captured_calls[0]["name"] == "list_agents"

    @pytest.mark.asyncio
    async def test_hindsight_422_normalizes_query(self, monkeypatch):
        from praxis.models.schemas import HermesRecallResult

        captured_calls: list[dict] = []

        async def mock_inner(tool_call, **kwargs):
            captured_calls.append(dict(tool_call))
            return "hermes_recall", HermesRecallResult(
                query=tool_call.get("query", ""),
                context_summary="recalled",
                success=True,
            )
        monkeypatch.setattr("praxis.graph.nodes._execute_tool_inner", mock_inner)

        d = ActionDispatcher(app=None)
        event = _event(
            tool_name="hermes_recall",
            error="422 validation error",
            pattern=RecoveryPattern.HINDSIGHT_422,
            action_type=RecoveryActionType.RETRY_WITH_ARGS,
            args={"query": "HELLO!@# World"},
        )
        result = await d.dispatch(event)
        assert result.success is True
        # Verify query was normalized
        assert captured_calls[0]["query"] == "hello world"

    @pytest.mark.asyncio
    async def test_retry_handles_exception(self, monkeypatch):
        async def mock_inner(tool_call, **kwargs):
            raise RuntimeError("boom")
        monkeypatch.setattr("praxis.graph.nodes._execute_tool_inner", mock_inner)

        d = ActionDispatcher(app=None)
        event = _event(
            tool_name="list_agents",
            pattern=RecoveryPattern.TOOL_RETURNED_EMPTY,
            action_type=RecoveryActionType.RETRY_WITH_ARGS,
            args={"region": "Australia"},
        )
        result = await d.dispatch(event)
        assert result.success is False
        assert "Retry failed" in result.message


class TestSwitchModel:
    @pytest.mark.asyncio
    async def test_switch_model_default_to_coder(self, monkeypatch):
        import praxis.graph.nodes as nodes
        nodes._MODEL_OVERRIDE = None  # Reset

        # Mock _replay_email since we don't have an event_id
        d = ActionDispatcher(app=None)
        event = _event(
            tool_name="react_loop",
            error="model emitted TOOL: text",
            pattern=RecoveryPattern.MODEL_EMITTED_TEXT_PROTOCOL,
            action_type=RecoveryActionType.SWITCH_MODEL,
            event_id="",  # no event_id, so no replay
        )
        result = await d.dispatch(event)
        assert result.success is True
        assert "umans-coder" in result.message
        assert nodes._MODEL_OVERRIDE == "umans-coder"
        # Cleanup
        nodes._MODEL_OVERRIDE = None

    @pytest.mark.asyncio
    async def test_switch_model_toggles_from_coder_to_flash(self, monkeypatch):
        import praxis.graph.nodes as nodes
        nodes._MODEL_OVERRIDE = "umans-coder"

        d = ActionDispatcher(app=None)
        event = _event(
            tool_name="react_loop",
            error="text protocol",
            pattern=RecoveryPattern.MODEL_EMITTED_TEXT_PROTOCOL,
            action_type=RecoveryActionType.SWITCH_MODEL,
            event_id="",
        )
        result = await d.dispatch(event)
        assert nodes._MODEL_OVERRIDE == "umans-flash"
        # Cleanup
        nodes._MODEL_OVERRIDE = None

    @pytest.mark.asyncio
    async def test_switch_model_with_event_id_replays(self, monkeypatch):
        import praxis.graph.nodes as nodes
        nodes._MODEL_OVERRIDE = None

        d = ActionDispatcher(app=None)
        # Mock _replay_email to avoid hitting the dead-letter log
        replay_called = []
        async def mock_replay(event):
            replay_called.append(event)
            return ActionResult(success=True, message="replayed ok")
        monkeypatch.setattr(d, "_replay_email", mock_replay)

        event = _event(
            tool_name="react_loop",
            error="text protocol",
            pattern=RecoveryPattern.MODEL_EMITTED_TEXT_PROTOCOL,
            action_type=RecoveryActionType.SWITCH_MODEL,
            event_id="evt_123",
        )
        result = await d.dispatch(event)
        assert result.success is True
        assert "replayed ok" in result.message
        assert len(replay_called) == 1
        # Cleanup
        nodes._MODEL_OVERRIDE = None


class TestReRegisterWebhook:
    @pytest.mark.asyncio
    async def test_re_register_deletes_old_creates_new_rotates(self, monkeypatch):
        from praxis.config import get_settings
        from praxis.services.agentmail_v2 import get_agentmail_v2

        # The dispatcher builds webhook_url as f"{settings.praxis_api_url}/webhook/email".
        # Default praxis_api_url is http://localhost:8000, so the mock URLs must
        # contain that string for the "delete matching" check to match.
        from praxis.config import get_settings
        expected_url = f"{get_settings().praxis_api_url}/webhook/email"

        # Mock the AgentMail client
        mock_client = MagicMock()
        mock_client.list_webhooks = AsyncMock(return_value=[
            MagicMock(id="wh_old1", url=expected_url),
            MagicMock(id="wh_other", url="https://other.example.com/webhook"),
        ])
        mock_client.delete_webhook = AsyncMock(return_value=None)
        mock_client.create_webhook = AsyncMock(return_value=MagicMock(id="wh_new123"))
        mock_client.rotate_webhook_secret = AsyncMock(return_value=MagicMock(secret="whsec_NEWSECRET123"))
        monkeypatch.setattr("praxis.services.agentmail_v2._v2_singleton", mock_client)

        # Mock env helpers
        written_env: dict[str, str] = {}
        def mock_read_env(path):
            return {"AGENTMAIL_WEBHOOK_SECRET": "whsec_OLD"}
        def mock_write_env(path, data):
            written_env.update(data)
        monkeypatch.setattr("praxis.webhooks.settings_admin._env_file_path", lambda: "/tmp/fake.env")
        monkeypatch.setattr("praxis.webhooks.settings_admin._read_env_as_dict", mock_read_env)
        monkeypatch.setattr("praxis.webhooks.settings_admin._write_env_dict", mock_write_env)

        d = ActionDispatcher(app=None)
        event = _event(
            tool_name="webhook_email_ingest",
            error="Invalid signature",
            pattern=RecoveryPattern.WEBHOOK_SECRET_MISMATCH,
            action_type=RecoveryActionType.RE_REGISTER_WEBHOOK,
        )
        result = await d.dispatch(event)
        assert result.success is True, result.message
        assert "wh_new123" in result.message
        # Old webhook pointing at our URL was deleted
        mock_client.delete_webhook.assert_called_once_with("wh_old1")
        # New webhook created
        mock_client.create_webhook.assert_called_once()
        # Secret was rotated
        mock_client.rotate_webhook_secret.assert_called_once_with("wh_new123")
        # .env was updated
        assert written_env.get("AGENTMAIL_WEBHOOK_SECRET") == "whsec_NEWSECRET123"

    @pytest.mark.asyncio
    async def test_re_register_handles_api_failure(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.list_webhooks = AsyncMock(side_effect=RuntimeError("api down"))
        monkeypatch.setattr("praxis.services.agentmail_v2._v2_singleton", mock_client)

        d = ActionDispatcher(app=None)
        event = _event(
            tool_name="webhook_email_ingest",
            pattern=RecoveryPattern.WEBHOOK_SECRET_MISMATCH,
            action_type=RecoveryActionType.RE_REGISTER_WEBHOOK,
        )
        result = await d.dispatch(event)
        assert result.success is False
        assert "api down" in result.message


class TestReplayEmail:
    @pytest.mark.asyncio
    async def test_replay_no_app_returns_failure(self):
        d = ActionDispatcher(app=None)
        event = _event(event_id="evt_123", action_type=RecoveryActionType.REPLAY_EMAIL)
        result = await d._replay_email(event)
        assert result.success is False
        assert "No app" in result.message

    @pytest.mark.asyncio
    async def test_replay_no_event_id_returns_failure(self):
        mock_app = MagicMock()
        d = ActionDispatcher(app=mock_app)
        event = _event(event_id="", action_type=RecoveryActionType.REPLAY_EMAIL)
        result = await d._replay_email(event)
        assert result.success is False
        assert "No event_id" in result.message

    @pytest.mark.asyncio
    async def test_replay_not_found_in_dead_letter(self, monkeypatch):
        mock_app = MagicMock()
        # Mock _read_failed_events to return empty list
        monkeypatch.setattr("praxis.webhooks.admin._read_failed_events", lambda settings: [])
        d = ActionDispatcher(app=mock_app)
        event = _event(event_id="evt_missing", action_type=RecoveryActionType.REPLAY_EMAIL)
        result = await d._replay_email(event)
        assert result.success is False
        assert "not found" in result.message


class TestBackoffRetry:
    @pytest.mark.asyncio
    async def test_backoff_retries_after_delay(self, monkeypatch):
        from praxis.models.schemas import CalculationResult

        sleep_calls: list[float] = []
        async def mock_sleep(delay):
            sleep_calls.append(delay)
        monkeypatch.setattr("praxis.recovery.actions.asyncio.sleep", mock_sleep)

        async def mock_inner(tool_call, **kwargs):
            return "dummy_calculator", CalculationResult(expression="2+2", value=4.0, success=True)
        monkeypatch.setattr("praxis.graph.nodes._execute_tool_inner", mock_inner)

        d = ActionDispatcher(app=None)
        event = _event(
            tool_name="dummy_calculator",
            pattern=RecoveryPattern.SERVICE_UNAVAILABLE,
            action_type=RecoveryActionType.BACKOFF_RETRY,
            args={"expression": "2+2"},
        )
        event.retry_count = 1  # 2^1 = 2s delay
        result = await d.dispatch(event)
        assert result.success is True
        assert len(sleep_calls) == 1
        assert sleep_calls[0] == 2  # 2^1 = 2

    @pytest.mark.asyncio
    async def test_backoff_caps_at_60s(self, monkeypatch):
        sleep_calls: list[float] = []
        async def mock_sleep(delay):
            sleep_calls.append(delay)
        monkeypatch.setattr("praxis.recovery.actions.asyncio.sleep", mock_sleep)

        async def mock_inner(tool_call, **kwargs):
            from praxis.models.schemas import CalculationResult
            return "dummy_calculator", CalculationResult(expression="x", value=0.0, success=True)
        monkeypatch.setattr("praxis.graph.nodes._execute_tool_inner", mock_inner)

        d = ActionDispatcher(app=None)
        event = _event(
            tool_name="dummy_calculator",
            pattern=RecoveryPattern.SERVICE_UNAVAILABLE,
            action_type=RecoveryActionType.BACKOFF_RETRY,
            args={},
        )
        event.retry_count = 10  # 2^10 = 1024, capped at 60
        await d.dispatch(event)
        assert sleep_calls[0] == 60


class TestEscalate:
    @pytest.mark.asyncio
    async def test_escalate_returns_failure_with_context(self):
        d = ActionDispatcher(app=None)
        event = _event(
            tool_name="mystery_tool",
            error="weird error",
            pattern=RecoveryPattern.UNKNOWN,
            action_type=RecoveryActionType.ESCALATE_MANUAL,
        )
        result = await d.dispatch(event)
        assert result.success is False
        assert "Escalated" in result.message
        assert "mystery_tool" in result.message


class TestGetServices:
    @pytest.mark.asyncio
    async def test_get_services_no_app_returns_none(self):
        d = ActionDispatcher(app=None)
        services = d._get_services()
        assert services["hermes_service"] is None
        assert services["agent_delegator"] is None

    @pytest.mark.asyncio
    async def test_get_services_extracts_from_app_state(self):
        mock_app = MagicMock()
        mock_app.state.hermes_service = "hermes_instance"
        mock_app.state.ldr_service = "ldr_instance"
        mock_app.state.document_service = None
        mock_app.state.agent_delegator = "delegator_instance"
        d = ActionDispatcher(app=mock_app)
        services = d._get_services()
        assert services["hermes_service"] == "hermes_instance"
        assert services["ldr_service"] == "ldr_instance"
        assert services["document_service"] is None
        assert services["agent_delegator"] == "delegator_instance"
