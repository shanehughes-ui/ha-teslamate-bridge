"""The one module that talks to Home Assistant.

Under the Supervisor the add-on is handed SUPERVISOR_TOKEN and reaches Core at
http://supervisor/core/api, so no long-lived token is stored anywhere. Outside it
(local development, the smoke test) HA_URL and HA_TOKEN are used instead.

One GET of /api/states per poll, cached for a few seconds: TeslaMate polls every
few seconds while it thinks the car is driving, and each of those must not become a
separate round trip to Home Assistant. Reads only. Nothing here can send a command
to the car -- there is no code path that POSTs to a service.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx

from .config import Config

Snapshot = dict[str, dict[str, Any]]


def resolve(cfg: Config, env: dict[str, str] | None = None) -> tuple[str, str]:
    """(api base url ending in /api, bearer token)."""
    env = dict(os.environ) if env is None else env
    sup = env.get("SUPERVISOR_TOKEN", "").strip()
    if sup:
        return "http://supervisor/core/api", sup
    url = (cfg.ha_url or env.get("HA_URL", "")).strip().rstrip("/")
    tok = (cfg.ha_token or env.get("HA_TOKEN", "")).strip()
    if not url or not tok:
        raise RuntimeError(
            "No way to reach Home Assistant: neither SUPERVISOR_TOKEN (add-on) nor "
            "HA_URL + HA_TOKEN (standalone) is set."
        )
    if not url.endswith("/api"):
        url = url + "/api"
    return url, tok


class HomeAssistant:
    def __init__(self, base_url: str, token: str, ttl_s: float = 5.0, timeout_s: float = 20.0) -> None:
        self._base = base_url
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=timeout_s,
        )
        self._ttl = ttl_s
        self._lock = asyncio.Lock()
        self._at = 0.0
        self._snap: Snapshot = {}

    async def snapshot(self) -> Snapshot:
        async with self._lock:
            if self._snap and (time.monotonic() - self._at) < self._ttl:
                return self._snap
            r = await self._client.get(f"{self._base}/states")
            r.raise_for_status()
            items = r.json()
            snap: Snapshot = {}
            for item in items:
                eid = item.get("entity_id")
                if eid:
                    snap[eid] = item
            self._snap = snap
            self._at = time.monotonic()
            return snap

    async def ping(self) -> bool:
        try:
            r = await self._client.get(f"{self._base}/")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        await self._client.aclose()
