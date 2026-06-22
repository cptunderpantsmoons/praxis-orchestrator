"""GET/POST /admin/settings — read/update app settings.

``GET`` returns the current settings with all secret fields masked (``"***"``
for non-empty secrets, ``""`` for empty ones). ``POST`` accepts a partial
update: the body is a JSON object of field-name → new-value pairs. If a secret
field is sent with the value ``"***"`` (the mask), the existing value is
preserved rather than overwritten — this lets the TUI round-trip the masked
settings form without clobbering secrets.

The update is persisted to the ``.env`` file resolved by ``_env_file_path()``
and the settings cache is cleared so the next request sees the new values.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from praxis.config import get_settings, reset_settings
from praxis.webhooks.auth import require_admin_token

router = APIRouter(prefix="/admin/settings", tags=["admin"])

# Fields whose values must never be echoed back in cleartext.
SECRET_FIELDS = {
    "umans_api_key",
    "agentmail_api_key",
    "agentmail_webhook_secret",
    "neo4j_password",
    "qdrant_api_key",
    "ldr_api_key",
    "hermes_api_key",
    "praxis_admin_token",
}

# All fields that may be read or updated through this endpoint. Anything not
# in this set is rejected with a 422 to prevent accidental writes to fields
# the admin UI does not understand.
ALLOWED_FIELDS = SECRET_FIELDS | {
    "umans_base_url",
    "neo4j_uri",
    "neo4j_user",
    "qdrant_url",
    "postgres_dsn",
    "ldr_url",
    "hermes_base_url",
    "umans_glm_context_window",
    "umans_kimi_context_window",
    "umans_qwen_context_window",
    "hermes_timeout",
    "hermes_max_retries",
    "environment",
    "default_region",
    "attachments_dir",
    "failed_events_path",
    "agentmail_inbox_id",
    "tool_protocol",
    "sender_style_enabled",
    "default_tone",
    "default_signature",
    "praxis_api_url",
    "host",
    "port",
}

_MASK = "***"


def _env_file_path() -> Path:
    """Return the .env file the settings loader reads from.

    Pulled out as a module-level function so tests can monkeypatch it.
    """
    settings = get_settings()
    env_file = settings.model_config.get("env_file") or ".env"
    # ``env_file`` may be a tuple in some configurations; take the first.
    if isinstance(env_file, (list, tuple)):
        env_file = env_file[0] if env_file else ".env"
    return Path(env_file)


def _read_env_as_dict(env_path: Path) -> dict[str, str]:
    """Parse a .env file into a dict of KEY=value pairs.

    Blank lines and ``#`` comments are skipped. Values are taken verbatim
    (no quote-stripping) — this matches pydantic-settings' default behaviour
    for simple values.
    """
    if not env_path.exists():
        return {}
    result: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        result[key.strip()] = value
    return result


def _write_env_dict(env_path: Path, data: dict[str, str]) -> None:
    """Write a dict back to a .env file, sorted by key for stable diffs."""
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in sorted(data.items())]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@router.get("")
async def get_current_settings(_: None = Depends(require_admin_token)) -> dict[str, Any]:
    """Return the current settings with secrets masked."""
    settings = get_settings()
    out: dict[str, Any] = {}
    for field in sorted(ALLOWED_FIELDS):
        value = getattr(settings, field, "")
        if field in SECRET_FIELDS:
            out[field] = _MASK if value else ""
        else:
            out[field] = value
    return out


@router.post("")
async def update_settings(
    patch: dict[str, Any],
    _: None = Depends(require_admin_token),
) -> dict[str, Any]:
    """Partially update settings, writing changes back to the .env file.

    A value of ``"***"`` for a secret field is treated as "no change" — the
    existing value is preserved. This lets the TUI POST the masked form back
    without clobbering secrets it cannot see.
    """
    invalid = set(patch.keys()) - ALLOWED_FIELDS
    if invalid:
        raise HTTPException(
            status_code=422, detail=f"Unknown fields: {sorted(invalid)}"
        )

    env_path = _env_file_path()
    existing = _read_env_as_dict(env_path)

    changed: list[str] = []
    for key, value in patch.items():
        if key in SECRET_FIELDS and value == _MASK:
            # Preserve existing secret
            continue
        existing[key.upper()] = str(value)
        changed.append(key)

    if changed:
        _write_env_dict(env_path, existing)
        reset_settings()

    return {"status": "updated", "fields": changed}
