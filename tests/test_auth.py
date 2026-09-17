"""Tests for authentication (dev mode bypass, JWT, API key)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import jwt
import pytest
from fastapi.testclient import TestClient

from app.auth import OrgContext
from app.config import get_settings
from app.main import app

client = TestClient(app)

_JWT_SECRET = "test-secret-for-jwt-at-least-32-bytes-long"


def _mock_org_context() -> OrgContext:
    return OrgContext(
        org_id="org-123",
        org_name="Test Org",
        tier="starter",
        rate_limit_hour=1000,
        predictions_limit_month=10000,
    )


@pytest.fixture()
def _enforce_auth():
    """Temporarily enable auth by setting supabase_url and jwt_secret."""
    settings = get_settings()
    orig_url = settings.supabase_url
    orig_secret = settings.supabase_jwt_secret
    settings.supabase_url = "https://fake.supabase.co"
    settings.supabase_jwt_secret = _JWT_SECRET
    yield
    settings.supabase_url = orig_url
    settings.supabase_jwt_secret = orig_secret


class TestDevMode:
    """Without SUPABASE_URL, auth is bypassed (conftest sets it to empty)."""

    def test_health_no_auth(self):
        resp = client.get("/v1/health")
        assert resp.status_code == 200

    def test_predictions_no_auth_in_dev_mode(self):
        """In dev mode requests without auth succeed."""
        resp = client.get("/v1/predictions")
        assert resp.status_code == 200


class TestAuthEnforced:
    """With SUPABASE_URL configured, auth is enforced."""

    def test_no_auth_returns_401(self, _enforce_auth):
        resp = client.get("/v1/predictions")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "UNAUTHORIZED"

    def test_invalid_jwt_returns_401(self, _enforce_auth):
        resp = client.get(
            "/v1/predictions",
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert resp.status_code == 401

    def test_valid_jwt_returns_200(self, _enforce_auth):
        token = jwt.encode(
            {"sub": "user-1", "aud": "authenticated", "app_metadata": {"org_id": "org-123"}},
            _JWT_SECRET,
            algorithm="HS256",
        )
        with patch("app.auth._fetch_org", new_callable=AsyncMock, return_value=_mock_org_context()):
            resp = client.get(
                "/v1/predictions",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200

    def test_invalid_api_key_returns_401(self, _enforce_auth):
        with patch("app.services.supabase.select", new_callable=AsyncMock, return_value=None):
            resp = client.get(
                "/v1/predictions",
                headers={"X-API-Key": "bad-key"},
            )
            assert resp.status_code == 401

    def test_valid_api_key_returns_200(self, _enforce_auth):
        api_key = "lgt_test_key_12345678"

        with patch("app.auth._validate_api_key", new_callable=AsyncMock, return_value=_mock_org_context()):
            resp = client.get(
                "/v1/predictions",
                headers={"X-API-Key": api_key},
            )
            assert resp.status_code == 200
