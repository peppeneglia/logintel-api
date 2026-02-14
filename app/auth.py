"""
Authentication dependency — validates JWT or API key.

In dev mode (SUPABASE_URL empty), returns a default org context so that
existing tests pass unchanged without auth headers.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

import jwt
from fastapi import Request

from app.config import get_settings
from app.errors import UnauthorizedError
from app.logging_config import org_id_var

logger = logging.getLogger(__name__)


@dataclass
class OrgContext:
    org_id: str
    org_name: str
    tier: str
    rate_limit_hour: int
    predictions_limit_month: int


_DEV_ORG = OrgContext(
    org_id="dev",
    org_name="Development",
    tier="professional",
    rate_limit_hour=1000,
    predictions_limit_month=10000,
)


async def get_current_org(request: Request) -> OrgContext:
    """FastAPI dependency that returns the authenticated org context."""
    settings = get_settings()

    # Dev mode bypass — no Supabase configured
    if not settings.supabase_url:
        org_id_var.set("dev")
        return _DEV_ORG

    # Try Bearer JWT first
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        return await _validate_jwt(token, settings.supabase_jwt_secret)

    # Try API key
    api_key = request.headers.get("X-API-Key", "")
    if api_key:
        return await _validate_api_key(api_key)

    raise UnauthorizedError("Missing authentication: provide Authorization Bearer or X-API-Key header")


async def _validate_jwt(token: str, secret: str) -> OrgContext:
    """Decode JWT, extract org_id from app_metadata, fetch org from DB."""
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"], options={"verify_aud": False})
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError(f"Invalid JWT: {exc}") from exc

    app_metadata = payload.get("app_metadata", {})
    org_id = app_metadata.get("org_id", "")
    if not org_id:
        raise UnauthorizedError("JWT missing org_id in app_metadata")

    return await _fetch_org(org_id)


async def _validate_api_key(key: str) -> OrgContext:
    """Hash the key, look up in api_keys table, return org context."""
    from app.services import supabase

    key_hash = hashlib.sha256(key.encode()).hexdigest()

    row = await supabase.select(
        "api_keys",
        params={
            "key_hash": f"eq.{key_hash}",
            "is_active": "eq.true",
            "select": "organization_id",
        },
        single=True,
    )

    if row is None:
        raise UnauthorizedError("Invalid API key")

    return await _fetch_org(row["organization_id"])


async def _fetch_org(org_id: str) -> OrgContext:
    """Fetch organization details from Supabase."""
    from app.services import supabase

    row = await supabase.select(
        "organizations",
        params={"id": f"eq.{org_id}", "select": "*"},
        single=True,
    )

    if row is None:
        raise UnauthorizedError("Organization not found")

    org = OrgContext(
        org_id=row["id"],
        org_name=row["name"],
        tier=row["tier"],
        rate_limit_hour=row.get("rate_limit_hour", 100),
        predictions_limit_month=row.get("predictions_limit_month", 1000),
    )
    org_id_var.set(org.org_id)
    return org
