"""Tests for the _execute_tool wrapper that captures failures for recovery."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from praxis.graph.nodes import _execute_tool, _execute_tool_inner, _MODEL_OVERRIDE, _get_router_and_model
from praxis.recovery.models import FailureContext


@pytest.fixture(autouse=True)
def reset_model_override():
    """Reset _MODEL_OVERRIDE between tests."""
    import praxis.graph.nodes as nodes
    old = nodes._MODEL_OVERRIDE
    nodes._MODEL_OVERRIDE = None
    yield
    nodes._MODEL_OVERRIDE = old


class TestModelOverride:
    def test_override_is_none_by_default(self):
        import praxis.graph.nodes as nodes
        assert nodes._MODEL_OVERRIDE is None

    def test_override_can_be_set(self):
        import praxis.graph.nodes as nodes
        nodes._MODEL_OVERRIDE = "umans-flash"
        assert nodes._MODEL_OVERRIDE == "umans-flash"
        # Cleanup
        nodes._MODEL_OVERRIDE = None

    def test_get_router_and_model_uses_override(self, monkeypatch):
        import praxis.graph.nodes as nodes
        # Mock UmansChatModel.create to capture what model_name it's called with
        calls = []
        class MockModel:
            pass
        class MockRouter:
            pass
        class MockChatModel:
            @staticmethod
            def create(model_name, router=None):
                calls.append(model_name)
                return MockModel()
        monkeypatch.setattr(nodes, "UmansChatModel", MockChatModel)
        nodes._MODEL_OVERRIDE = "umans-flash"
        _get_router_and_model("umans-coder")
        assert calls == ["umans-flash"]
        nodes._MODEL_OVERRIDE = None

    def test_get_router_and_model_falls_back_when_no_override(self, monkeypatch):
        import praxis.graph.nodes as nodes
        calls = []
        class MockModel:
            pass
        class MockChatModel:
            @staticmethod
            def create(model_name, router=None):
                calls.append(model_name)
                return MockModel()
        monkeypatch.setattr(nodes, "UmansChatModel", MockChatModel)
        nodes._MODEL_OVERRIDE = None
        _get_router_and_model("umans-coder")
        assert calls == ["umans-coder"]


class TestExecuteToolWrapper:
    @pytest.mark.asyncio
    async def test_wrapper_passes_through_success(self, monkeypatch):
        """When the tool succeeds, no capture happens."""
        from praxis.models.schemas import CalculationResult

        async def mock_inner(tool_call, **kwargs):
            return "dummy_calculator", CalculationResult(expression="2+2", value=4.0)
        monkeypatch.setattr("praxis.graph.nodes._execute_tool_inner", mock_inner)

        # Ensure no recovery manager is set
        import praxis.recovery as rec
        rec.set_recovery_manager(None)

        tool_call = {"name": "dummy_calculator", "expression": "2+2"}
        name, result = await _execute_tool(tool_call)
        assert name == "dummy_calculator"
        assert result.success is True
        assert result.value == 4.0

    @pytest.mark.asyncio
    async def test_wrapper_captures_failure_when_manager_set(self, monkeypatch):
        """When the tool fails and a manager is set, capture_failure is called."""
        from praxis.models.schemas import HermesLearnResult

        async def mock_inner(tool_call, **kwargs):
            return "hermes_learn", HermesLearnResult(correction_text="x", success=False)
        monkeypatch.setattr("praxis.graph.nodes._execute_tool_inner", mock_inner)

        mock_mgr = MagicMock()
        mock_mgr.capture_failure = MagicMock()
        import praxis.recovery as rec
        rec.set_recovery_manager(mock_mgr)
        try:
            tool_call = {"name": "hermes_learn", "correction": "x"}
            name, result = await _execute_tool(tool_call)
            assert name == "hermes_learn"
            assert result.success is False
            # capture_failure should have been called
            assert mock_mgr.capture_failure.called
            call_kwargs = mock_mgr.capture_failure.call_args.kwargs
            assert call_kwargs["tool_name"] == "hermes_learn"
            assert "correction" in call_kwargs["args"]  # name was popped, correction remains
        finally:
            rec.set_recovery_manager(None)

    @pytest.mark.asyncio
    async def test_wrapper_skips_capture_when_no_manager(self, monkeypatch):
        """When no manager is set, the wrapper still returns the result."""
        from praxis.models.schemas import HermesLearnResult

        async def mock_inner(tool_call, **kwargs):
            return "hermes_learn", HermesLearnResult(correction_text="x", success=False)
        monkeypatch.setattr("praxis.graph.nodes._execute_tool_inner", mock_inner)

        import praxis.recovery as rec
        rec.set_recovery_manager(None)

        tool_call = {"name": "hermes_learn", "correction": "x"}
        name, result = await _execute_tool(tool_call)
        assert result.success is False  # Still returned, just not captured

    @pytest.mark.asyncio
    async def test_wrapper_swallows_capture_exceptions(self, monkeypatch):
        """If capture_failure raises, the wrapper still returns the result."""
        from praxis.models.schemas import HermesLearnResult

        async def mock_inner(tool_call, **kwargs):
            return "hermes_learn", HermesLearnResult(correction_text="x", success=False)
        monkeypatch.setattr("praxis.graph.nodes._execute_tool_inner", mock_inner)

        mock_mgr = MagicMock()
        mock_mgr.capture_failure = MagicMock(side_effect=RuntimeError("capture crashed"))
        import praxis.recovery as rec
        rec.set_recovery_manager(mock_mgr)
        try:
            tool_call = {"name": "hermes_learn", "correction": "x"}
            # Should NOT raise
            name, result = await _execute_tool(tool_call)
            assert name == "hermes_learn"
            assert result.success is False
        finally:
            rec.set_recovery_manager(None)

