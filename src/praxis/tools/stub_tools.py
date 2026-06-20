"""Stub tools for testing the ReAct agent without real LLM calls.

These tools provide deterministic outputs for testing the agent's
tool-calling loop without requiring external dependencies. Per REQ-205,
the two ReAct registry tools are ``dummy_search`` and ``dummy_calculator``;
both return strictly typed Pydantic models (never raw strings) so the
graph state machine can validate outputs against its contracts.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from praxis.models.schemas import CalculationResult, SearchResult

# Import email tools
from praxis.tools.email_tools import reply_email_tool, send_email_tool


@tool
def dummy_search(query: str) -> SearchResult:
    """Search for information about a topic (REQ-205 stub).

    Returns a strictly typed :class:`SearchResult` so the ReAct orchestrator
    never has to handle raw dicts or strings.

    Args:
        query: Free-form search query.

    Returns:
        ``SearchResult`` with deterministic mock hits.
    """
    return SearchResult(
        query=query,
        results=[
            f"PRAXIS Status: System is operational (matched '{query}').",
            f"Knowledge base: no live documents found for '{query}'.",
        ],
    )


@tool
def dummy_calculator(expression: str) -> CalculationResult:
    """Evaluate a mathematical expression (REQ-205 stub).

    Uses a constrained AST walker so we structurally reject attribute
    access, calls, and imports (no ``eval()``). On failure we return a
    successful ``CalculationResult`` with ``value=0.0`` and a non-empty
    ``unit`` describing the error — this keeps the return type stable
    for the ReAct loop.

    Args:
        expression: Arithmetic expression, e.g. ``"2 + 3"`` or ``"(10*3)-2"``.

    Returns:
        ``CalculationResult`` with the evaluated ``value`` (or 0.0 on error).
    """
    import ast
    import operator

    operators = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Pow: operator.pow,
        ast.USub: operator.neg,
        ast.UAdd: operator.pos,
    }

    def _eval(node: ast.AST) -> Any:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp):
            op = operators.get(type(node.op))
            if op is None:
                raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
            return op(_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp):
            op = operators.get(type(node.op))
            if op is None:
                raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
            return op(_eval(node.operand))
        raise ValueError(f"Unsupported expression: {type(node).__name__}")

    try:
        tree = ast.parse(expression, mode="eval")
        value = float(_eval(tree.body))
        return CalculationResult(expression=expression, value=value, unit="")
    except Exception as exc:
        return CalculationResult(
            expression=expression,
            value=0.0,
            unit=f"error: {exc}",
        )


# ── Backward-compatible aliases ─────────────────────────────────────
# Earlier scaffolding (test_stub_tools.py) used these names. They keep the
# the same return shape (raw dict) and remain in ALL_TOOLS for the
# general-purpose tool registry, but ReAct orchestration MUST use
# dummy_search / dummy_calculator above (REQ-205).


@tool
def search_tool(query: str) -> dict[str, Any]:
    """Search for information about a topic.

    This is a stub tool that returns mock search results for testing.

    Args:
        query: Search query string

    Returns:
        Dictionary with search results
    """
    return {
        "query": query,
        "results": [
            {
                "title": f"Result 1 for: {query}",
                "snippet": f"This is a mock result about {query}",
                "url": f"https://example.com/search?q={query.replace(' ', '+')}",
            },
            {
                "title": f"Result 2 for: {query}",
                "snippet": f"Another mock result about {query}",
                "url": f"https://example.org/search?q={query.replace(' ', '+')}",
            },
        ],
        "total_results": 2,
    }


@tool
def calculator_tool(expression: str) -> dict[str, Any]:
    """Evaluate a mathematical expression.

    This tool safely evaluates mathematical expressions using ast.literal_eval.

    Args:
        expression: Mathematical expression to evaluate (e.g., "2 + 2", "10 * 5")

    Returns:
        Dictionary with the result or error message
    """
    import ast
    import operator

    # Safe operators
    operators = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Pow: operator.pow,
        ast.USub: operator.neg,
        ast.UAdd: operator.pos,
    }

    def _eval(node: ast.AST) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        elif isinstance(node, ast.BinOp):
            left = _eval(node.left)
            right = _eval(node.right)
            op = operators.get(type(node.op))
            if op is None:
                raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
            return op(left, right)
        elif isinstance(node, ast.UnaryOp):
            operand = _eval(node.operand)
            op = operators.get(type(node.op))
            if op is None:
                raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
            return op(operand)
        else:
            raise ValueError(f"Unsupported expression: {type(node).__name__}")

    try:
        tree = ast.parse(expression, mode="eval")
        result = _eval(tree.body)
        return {
            "expression": expression,
            "result": result,
            "success": True,
        }
    except Exception as e:
        return {
            "expression": expression,
            "error": str(e),
            "success": False,
        }


@tool
def dummy_tool(input_text: str) -> str:
    """A dummy tool that echoes back the input.

    Useful for testing tool invocation without side effects.

    Args:
        input_text: Text to echo back

    Returns:
        The input text unchanged
    """
    return input_text


# Export all tools for the ReAct agent
ALL_TOOLS = [
    search_tool,
    calculator_tool,
    dummy_tool,
    send_email_tool,
    reply_email_tool,
]

# REQ-205: typed-Pydantic tools bound to the ReAct agent
REACT_TOOLS = [
    dummy_search,
    dummy_calculator,
]
