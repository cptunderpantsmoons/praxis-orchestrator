"""Template loader for PRAXIS prompt templates.

Loads versioned YAML templates, renders them with Jinja2 variable substitution,
and provides a clean API for the graph nodes to access prompt templates.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import jinja2
import structlog
import yaml

logger = structlog.get_logger()

# Default template directory (can be overridden via settings)
DEFAULT_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


class TemplateError(Exception):
    """Raised when a template fails to load or render."""

    pass


class TemplateLoader:
    """Load and render versioned YAML prompt templates.

    Attributes:
        template_dir: Directory containing YAML template files.
        _cache: Cache of loaded templates keyed by template path.
        _env: Jinja2 environment for rendering.
    """

    def __init__(self, template_dir: Path | None = None) -> None:
        """Initialize the template loader.

        Args:
            template_dir: Directory containing YAML templates.
                Defaults to the templates/ directory alongside this module.
        """
        self.template_dir = template_dir or DEFAULT_TEMPLATE_DIR
        self._cache: dict[str, dict[str, Any]] = {}
        self._env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(self.template_dir)),
            autoescape=True,  # Templates are versioned YAML (trusted); autoescape protects against injection
        )

    def load_template(self, template_path: str | Path) -> dict[str, Any]:
        """Load a YAML template file into memory.

        Args:
            template_path: Path to the YAML template file (relative to template_dir).

        Returns:
            Dict with keys: template, variables, system_prompt, user_prompt, version.

        Raises:
            TemplateError: If the file doesn't exist or fails to parse.
        """
        key = str(template_path)
        if key in self._cache:
            return self._cache[key]

        full_path = self.template_dir / template_path
        if not full_path.exists():
            raise TemplateError(f"Template not found: {full_path}")

        try:
            with open(full_path) as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            raise TemplateError(f"Failed to parse template {full_path}: {exc}") from exc

        # Validate required keys
        required = ["template", "system_prompt"]
        for key in required:
            if key not in data:
                raise TemplateError(f"Template missing required key: {key}")

        self._cache[key] = data
        logger.info("template.loaded", path=str(template_path), keys=list(data.keys()))
        return data

    def render(self, template: dict[str, Any], variables: dict[str, Any]) -> str:
        """Render a template with variable substitution using Jinja2.

        Args:
            template: Loaded template dict (from load_template).
            variables: Dict of variable names to values.

        Returns:
            Rendered template string.

        Raises:
            TemplateError: If rendering fails.
        """
        template_str = template.get("template", "")
        if not template_str:
            return ""

        try:
            jinja_template = self._env.from_string(template_str)
            return jinja_template.render(**variables)
        except jinja2.TemplateError as exc:
            raise TemplateError(f"Failed to render template: {exc}") from exc

    def render_from_path(
        self, template_path: str | Path, variables: dict[str, Any]
    ) -> str:
        """Load and render a template in one step.

        Args:
            template_path: Path to the YAML template file.
            variables: Dict of variable names to values.

        Returns:
            Rendered template string.
        """
        template = self.load_template(template_path)
        return self.render(template, variables)

    def list_templates(self) -> list[str]:
        """List all available template file names.

        Returns:
            List of template file names (e.g., ['triage_system_v1.yaml', ...]).
        """
        if not self.template_dir.exists():
            return []
        return sorted(
            f.name for f in self.template_dir.iterdir() if f.suffix == ".yaml"
        )


# Module-level singleton for convenience
_default_loader: TemplateLoader | None = None


def get_template_loader(template_dir: Path | None = None) -> TemplateLoader:
    """Get or create the default template loader.

    Args:
        template_dir: Optional override for the template directory.

    Returns:
        TemplateLoader instance.
    """
    global _default_loader
    if _default_loader is None or (template_dir is not None and template_dir != _default_loader.template_dir):
        _default_loader = TemplateLoader(template_dir=template_dir)
    return _default_loader
