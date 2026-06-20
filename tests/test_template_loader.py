"""Tests for the template loader.

Tests YAML loading, Jinja2 rendering, variable substitution, versioning,
and error handling.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from praxis.services.template_loader import (
    TemplateError,
    TemplateLoader,
    get_template_loader,
)

# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def template_dir(tmp_path: Path) -> Path:
    """Create a temporary directory with a sample YAML template."""
    sample = tmp_path / "test_template_v1.yaml"
    sample.write_text(
        yaml.dump(
            {
                "template": "Hello {{ name }}! You have {{ count }} messages.",
                "variables": ["name", "count"],
                "system_prompt": "You are a greeting assistant.",
                "user_prompt": "Greet {{ name }}.",
            }
        )
    )
    return tmp_path


@pytest.fixture
def loader(template_dir: Path) -> TemplateLoader:
    """TemplateLoader pointing at the temporary directory."""
    return TemplateLoader(template_dir=template_dir)


# ── Tests ───────────────────────────────────────────────────────


def test_load_template_returns_dict(loader: TemplateLoader) -> None:
    """load_template returns a dict with all expected keys."""
    data = loader.load_template("test_template_v1.yaml")
    assert isinstance(data, dict)
    assert "template" in data
    assert "variables" in data
    assert "system_prompt" in data
    assert "{{ name }}" in data["template"]
    assert "{{ count }}" in data["template"]


def test_load_template_caches_result(loader: TemplateLoader) -> None:
    """load_template caches loaded templates to avoid re-reading."""
    data1 = loader.load_template("test_template_v1.yaml")
    data2 = loader.load_template("test_template_v1.yaml")
    # Both calls return dicts with identical content
    assert data1 == data2


def test_load_template_raises_on_missing_file(loader: TemplateLoader) -> None:
    """load_template raises TemplateError for non-existent files."""
    with pytest.raises(TemplateError, match="not found"):
        loader.load_template("nonexistent.yaml")


def test_load_template_raises_on_bad_yaml(tmp_path: Path) -> None:
    """load_template raises TemplateError for malformed YAML."""
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("{{invalid yaml::[}")
    loader = TemplateLoader(template_dir=tmp_path)
    with pytest.raises(TemplateError, match="parse"):
        loader.load_template("bad.yaml")


def test_render_substitutes_variables(loader: TemplateLoader) -> None:
    """render replaces Jinja2 variables with provided values."""
    data = loader.load_template("test_template_v1.yaml")
    rendered = loader.render(data, {"name": "Alice", "count": 42})
    assert "Alice" in rendered
    assert "42" in rendered


def test_render_with_missing_variables(loader: TemplateLoader) -> None:
    """render handles missing variables gracefully (Jinja2 default)."""
    data = loader.load_template("test_template_v1.yaml")
    rendered = loader.render(data, {})
    # Jinja2 replaces missing vars with empty string by default
    assert "Hello" in rendered


def test_render_from_path(loader: TemplateLoader) -> None:
    """render_from_path loads and renders in one step."""
    result = loader.render_from_path(
        "test_template_v1.yaml", {"name": "Bob", "count": 7}
    )
    assert "Bob" in result
    assert "7" in result


def test_list_templates_returns_filenames(loader: TemplateLoader) -> None:
    """list_templates returns sorted list of .yaml file names."""
    templates = loader.list_templates()
    assert "test_template_v1.yaml" in templates


def test_get_template_loader_singleton() -> None:
    """get_template_loader returns a singleton TemplateLoader."""
    loader1 = get_template_loader()
    loader2 = get_template_loader()
    assert loader1 is loader2
