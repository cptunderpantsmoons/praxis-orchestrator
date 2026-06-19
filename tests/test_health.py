"""Tests for the GET /health endpoint — Quality Gate 1 criterion 1."""

from fastapi.testclient import TestClient


def test_health_check_returns_200(client: TestClient):
    """Application boots successfully and responds to health check."""
    response = client.get("/health")
    assert response.status_code == 200


def test_health_check_returns_ok_status(client: TestClient):
    """Health response status is 'ok'."""
    response = client.get("/health")
    data = response.json()
    assert data["status"] == "ok"


def test_health_check_includes_version(client: TestClient):
    """Health response includes the application version."""
    response = client.get("/health")
    data = response.json()
    assert "version" in data
    assert data["version"] != ""


def test_health_check_includes_services(client: TestClient):
    """Health response includes sub-service statuses."""
    response = client.get("/health")
    data = response.json()
    assert "services" in data
    assert data["services"]["api"] == "ready"
