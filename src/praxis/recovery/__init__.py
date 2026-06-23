"""Auto-healing recovery agent package.

Provides a module-level singleton accessor so callers in the ReAct loop
(`praxis.graph.nodes`) can fire-and-forget failure captures without holding
a reference to the FastAPI app.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from praxis.recovery.models import (
    FailureContext,
    RecoveryActionType,
    RecoveryEvent,
    RecoveryPattern,
    RecoveryStats,
)

if TYPE_CHECKING:
    from praxis.recovery.manager import RecoveryManager

_recovery_manager: RecoveryManager | None = None


def get_recovery_manager() -> RecoveryManager | None:
    """Return the process-wide RecoveryManager, or None if not initialized."""
    return _recovery_manager


def set_recovery_manager(mgr: RecoveryManager | None) -> None:
    """Set the process-wide RecoveryManager. Called once from lifespan()."""
    global _recovery_manager
    _recovery_manager = mgr


__all__ = [
    "FailureContext",
    "RecoveryActionType",
    "RecoveryEvent",
    "RecoveryPattern",
    "RecoveryStats",
    "get_recovery_manager",
    "set_recovery_manager",
]
