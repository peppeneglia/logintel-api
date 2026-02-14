"""
Supabase PostgREST async client — FRD Section 4.2.

Thin wrapper over the shared httpx client to interact with Supabase's
PostgREST API.  Follows the same init-at-startup pattern as cache.py.

Uses the service_role key (bypasses RLS) for all operations.
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.http_client import get_client

logger = logging.getLogger(__name__)

_base_url: str = ""
_headers: dict[str, str] = {}
_configured: bool = False


def init_supabase() -> None:
    """Configure PostgREST base URL and auth headers from settings."""
    global _base_url, _headers, _configured

    from app.config import get_settings

    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_key:
        logger.warning("SUPABASE_URL or SUPABASE_SERVICE_KEY not set — Supabase disabled")
        _configured = False
        return

    _base_url = settings.supabase_url.rstrip("/") + "/rest/v1"
    _headers = {
        "apikey": settings.supabase_service_key,
        "Authorization": f"Bearer {settings.supabase_service_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    _configured = True
    logger.info("Supabase PostgREST client initialised (%s)", _base_url)


def is_configured() -> bool:
    return _configured


async def select(
    table: str,
    params: dict[str, str] | None = None,
    single: bool = False,
) -> list[dict[str, Any]] | dict[str, Any] | None:
    """
    SELECT from a PostgREST table.

    If *single* is True, returns a single dict or None.
    Otherwise returns a list of dicts.
    """
    client = get_client()
    url = f"{_base_url}/{table}"
    headers = dict(_headers)
    if single:
        headers["Accept"] = "application/vnd.pgrst.object+json"
    else:
        headers["Accept"] = "application/json"

    resp = await client.get(url, params=params or {}, headers=headers)

    if single and resp.status_code == 406:
        return None
    resp.raise_for_status()
    return resp.json()


async def insert(table: str, data: dict[str, Any]) -> dict[str, Any]:
    """INSERT a row into a PostgREST table and return the created row."""
    client = get_client()
    url = f"{_base_url}/{table}"
    resp = await client.post(url, json=data, headers=_headers)
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if isinstance(rows, list) else rows
