"""The Tesla Owner API surface that TeslaMate actually calls, served from Home
Assistant state. Wiring only -- every judgement lives in the pure modules.

TeslaMate is pointed here with TESLA_API_HOST / TESLA_AUTH_HOST, which is a supported,
documented configuration (it is how the third-party providers work). TeslaMate itself
runs stock and unmodified and never learns it is not talking to Tesla.

Auth is deliberately a no-op. This service is only reachable on the add-on network,
the only client that can reach it is TeslaMate, and the real credential (the Supervisor
token) never leaves this process. OAuth between two containers that trust each other by
construction would be theatre.

READ-ONLY BY CONSTRUCTION. There is no code path that can send a command to the car:
the only upstream call in the whole program is a GET of /api/states, and every
command-shaped route below is refused with 405 and logged. Never add one.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import __version__, config, hass, mapping
from .sanitise import Tracker

log = logging.getLogger("ha-teslamate-bridge")

cfg = config.load()
logging.basicConfig(
    level=getattr(logging, cfg.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
)

# No auto-generated docs: the surface is four endpoints and nothing should invite
# browsing it. /health is the operator's view.
app = FastAPI(title="ha-teslamate-bridge", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
_ha: hass.HomeAssistant | None = None
_tracker = Tracker(
    max_kmh=cfg.max_plausible_kmh,
    stale_after_s=float(cfg.stale_after_seconds),
    grace_s=float(cfg.online_grace_seconds),
)
_id = mapping.tesla_id(cfg.vin)

UNAVAILABLE = {"error": 'vehicle unavailable: {:error=>"vehicle unavailable"}'}


@app.on_event("startup")
async def _startup() -> None:
    global _ha
    base, token = hass.resolve(cfg)
    _ha = hass.HomeAssistant(base, token, ttl_s=cfg.cache_ttl_seconds)
    log.info(
        "bridge %s: car %r vin %s id %d; reading %s; entities from prefix %r",
        __version__, cfg.display_name, cfg.vin, _id, base, cfg.car_entity_prefix,
    )
    if not await _ha.ping():
        log.warning("Home Assistant did not answer at %s -- will keep trying per poll", base)


@app.on_event("shutdown")
async def _shutdown() -> None:
    if _ha:
        await _ha.close()


@app.middleware("http")
async def _log_requests(request: Request, call_next):  # type: ignore[no-untyped-def]
    t0 = time.monotonic()
    response = await call_next(request)
    log.info("%s %s -> %d (%.0f ms)", request.method, request.url.path, response.status_code, (time.monotonic() - t0) * 1000)
    return response


async def _bundle() -> mapping.Bundle | None:
    assert _ha is not None
    try:
        snap = await _ha.snapshot()
    except Exception as exc:  # noqa: BLE001 -- surfaced as a 503, not a crash
        log.warning("Home Assistant read failed: %s", exc)
        return None
    b = mapping.build(cfg, snap, _tracker, time.time())
    if _tracker.last_reason and _tracker.last_reason not in ("ok", "stationary", "first fix"):
        log.debug("fix: %s", _tracker.last_reason)
    return b


def _unavailable_ha() -> JSONResponse:
    return JSONResponse(status_code=503, content={"error": "home assistant unavailable"})


# ── auth: accepted unconditionally, see module docstring ───────────────────────────

_TOKEN = {
    "access_token": "ha-teslamate-bridge",
    "refresh_token": "ha-teslamate-bridge",
    "id_token": "ha-teslamate-bridge",
    "expires_in": 28800,
    "token_type": "Bearer",
}


@app.post("/oauth2/v3/token")
@app.post("/api/oauth2/v3/token")
@app.post("/api/1/auth/token")
async def token() -> dict[str, Any]:
    return dict(_TOKEN)


@app.get("/api/1/users/region")
async def region(request: Request) -> dict[str, Any]:
    base = str(request.base_url).rstrip("/")
    return {"response": {"region": "other", "fleet_api_base_url": base}}


# ── the Owner API surface TeslaMate polls ──────────────────────────────────────────


@app.get("/api/1/products")
@app.get("/api/1/vehicles")
async def products() -> Any:
    b = await _bundle()
    if b is None:
        return _unavailable_ha()
    return {"response": [b.summary], "count": 1}


@app.get("/api/1/vehicles/{vid}")
async def vehicle(vid: int) -> Any:
    if vid != _id:
        return JSONResponse(status_code=404, content={"error": "not_found"})
    b = await _bundle()
    if b is None:
        return _unavailable_ha()
    return {"response": b.summary}


@app.get("/api/1/vehicles/{vid}/vehicle_data")
async def vehicle_data(vid: int) -> Any:
    if vid != _id:
        return JSONResponse(status_code=404, content={"error": "not_found"})
    b = await _bundle()
    if b is None:
        return _unavailable_ha()
    # A parked, unplugged car must 408, not return data. TeslaMate uses that response
    # to record `asleep` and stop polling hard; a payload would make it believe the car
    # is awake forever and invent a ten-hour stationary drive.
    if b.asleep:
        return JSONResponse(status_code=408, content=UNAVAILABLE)
    return {"response": b.data}


# ── refused: this bridge can never act on the car ──────────────────────────────────


@app.api_route("/api/1/vehicles/{vid}/command/{cmd:path}", methods=["POST", "GET", "PUT"])
@app.api_route("/api/1/vehicles/{vid}/wake_up", methods=["POST", "GET"])
async def refuse(vid: int, cmd: str = "") -> Any:
    log.warning("refused command request for vehicle %s: %r (this bridge is read-only)", vid, cmd or "wake_up")
    return JSONResponse(status_code=405, content={"error": "this bridge is read-only; commands are refused"})


# ── ops ────────────────────────────────────────────────────────────────────────────


@app.get("/health")
async def health() -> Any:
    b = await _bundle()
    if b is None:
        return JSONResponse(status_code=503, content={"ok": False, "error": "home assistant unavailable"})
    ds = b.data["drive_state"]
    cs = b.data["charge_state"]
    return {
        "ok": True,
        "version": __version__,
        "car": {"name": cfg.display_name, "id": b.tesla_id, "state": b.summary["state"]},
        "soc": cs["battery_level"],
        "charging_state": cs["charging_state"],
        "shift_state": ds["shift_state"],
        "odometer_mi": b.data["vehicle_state"]["odometer"],
        "has_fix": ds["latitude"] is not None,
        "last_fix_reason": _tracker.last_reason,
        "under_supervisor": bool(os.environ.get("SUPERVISOR_TOKEN")),
    }
