"""Home Assistant entity snapshot -> Tesla Owner API JSON. Pure.

Input is a plain snapshot: {entity_id: {"state": str, "attributes": {...},
"last_updated": iso8601}} exactly as /api/states returns it, plus the Config and a
Tracker. No I/O, no clock -- `now` is passed in. That is what lets a recorded
snapshot be replayed in a test and asserted against the key set TeslaMate expects.

The shape follows what a working shim (epheterson/lucid-teslamate-bridge) already
serves to current TeslaMate, field for field. Two rules from the plan are applied
throughout:

  * UNITS: the Owner API is miles and mph. Every distance below goes through units.py.
  * ABSENT MEANS ABSENT: a signal the car does not carry is None, never zero. A zero
    outside temperature or a zero range is a fact TeslaMate would store; None is not.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .config import Config
from .sanitise import Fix, Tracker, charging_state, shift_state
from .units import km_to_mi, kmh_to_mph, psi_to_bar

API_VERSION = 71
# A constant, on purpose. The car exposes no software version, so TeslaMate's
# `updates` table stays empty. That is correct, not a gap.
CAR_VERSION = "ha-teslamate-bridge"
# Physically possible ambient range; the source integration can emit transient spikes.
TEMP_MIN_C, TEMP_MAX_C = -60.0, 70.0

Snapshot = dict[str, dict[str, Any]]


@dataclass(frozen=True)
class Bundle:
    summary: dict[str, Any]
    data: dict[str, Any]
    asleep: bool
    tesla_id: int


# --- reading the snapshot -----------------------------------------------------------

_MISSING = frozenset({"", "unknown", "unavailable", "none", "null"})


def _raw(snap: Snapshot, eid: str) -> str | None:
    e = snap.get(eid)
    if not e:
        return None
    s = e.get("state")
    if s is None or str(s).strip().lower() in _MISSING:
        return None
    return str(s)


def _num(snap: Snapshot, eid: str) -> float | None:
    s = _raw(snap, eid)
    if s is None:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _attr(snap: Snapshot, eid: str, key: str) -> Any:
    e = snap.get(eid)
    if not e:
        return None
    return (e.get("attributes") or {}).get(key)


def _attr_num(snap: Snapshot, eid: str, key: str) -> float | None:
    v = _attr(snap, eid, key)
    if v is None or (isinstance(v, str) and v.strip().lower() in _MISSING):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _on(snap: Snapshot, eid: str) -> bool | None:
    s = _raw(snap, eid)
    if s is None:
        return None
    return s.strip().lower() in {"on", "true", "open", "locked"}


def _epoch(value: Any) -> float | None:
    """ISO-8601 -> epoch seconds. Tolerates a trailing Z and missing tzinfo (UTC)."""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in _MISSING:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _plausible_temp(c: float | None) -> float | None:
    if c is None:
        return None
    return c if TEMP_MIN_C <= c <= TEMP_MAX_C else None


def tesla_id(vin: str) -> int:
    """A stable pseudo-Tesla id. TeslaMate keys the car on it forever, so it must never
    change across restarts: hash the VIN and there is no state to persist."""
    return int(hashlib.sha256(vin.encode()).hexdigest()[:12], 16)


# --- the pieces ---------------------------------------------------------------------


def _fix(cfg: Config, snap: Snapshot) -> Fix | None:
    eid = cfg.entity("location")
    e = snap.get(eid)
    if not e:
        return None
    lat = _attr_num(snap, eid, "latitude")
    lon = _attr_num(snap, eid, "longitude")
    at = _epoch(e.get("last_updated"))
    if lat is None or lon is None or at is None:
        return None
    return Fix(lat=lat, lon=lon, at=at)


def _session_energy(cfg: Config, snap: Snapshot) -> float | None:
    v = _num(snap, cfg.session_energy_entity)
    if v is None and cfg.session_energy_fallback_entity:
        v = _num(snap, cfg.session_energy_fallback_entity)
    return v


def _door(snap: Snapshot, eid: str) -> int:
    # Tesla encodes doors as 0 closed / non-zero open.
    return 1 if _on(snap, eid) else 0


def _int(v: float | None) -> int | None:
    return None if v is None else int(round(v))


def vehicle_summary(cfg: Config, tid: int, asleep: bool) -> dict[str, Any]:
    """/api/1/products and /api/1/vehicles/{id}. TeslaMate filters /products for
    entries carrying "vehicle_id" -- that key is not optional."""
    return {
        "id": tid,
        "vehicle_id": tid,
        "vin": cfg.vin,
        "display_name": cfg.display_name,
        "option_codes": "",
        "color": None,
        "access_type": "OWNER",
        "tokens": [],
        "state": "asleep" if asleep else "online",
        "in_service": False,
        "id_s": str(tid),
        "calendar_enabled": False,
        "api_version": API_VERSION,
        "backseat_token": None,
        "backseat_token_updated_at": None,
    }


def charge_state(cfg: Config, snap: Snapshot, ts_ms: int) -> dict[str, Any]:
    e = cfg.entity
    soc = _num(snap, e("soc"))
    range_mi = km_to_mi(_num(snap, e("range_km")))
    plug = _on(snap, e("charger_plug"))
    state = charging_state(_raw(snap, e("charger_connection")), plug)
    kw = _num(snap, e("charging_power_kw"))
    supply = str(_attr(snap, e("charging_power_kw"), "supply") or "").upper()
    phases = _attr_num(snap, e("charging_power_kw"), "phases")
    dc = supply == "DC" or _num(snap, e("dc_charge_current_a")) not in (None, 0.0)
    amps = _num(snap, e("dc_charge_current_a")) if dc else _num(snap, e("charge_current_a"))
    volts = _num(snap, e("dc_charge_voltage_v")) if dc else _num(snap, e("charge_voltage_v"))
    ttf_min = _num(snap, e("time_to_full_min"))
    charging = state == "Charging"

    return {
        "battery_level": _int(soc),
        "usable_battery_level": _int(soc),
        "battery_range": range_mi,
        "est_battery_range": range_mi,
        "ideal_battery_range": range_mi,
        "charging_state": state,
        "charger_power": _int(kw) if charging else 0,
        "charge_rate": None,
        "charge_energy_added": _session_energy(cfg, snap) if plug or charging else None,
        "charge_miles_added_rated": None,
        "charge_miles_added_ideal": None,
        "charge_limit_soc": None,
        "charge_port_door_open": bool(plug),
        "charger_voltage": _int(volts) if charging else 0,
        "charger_actual_current": _int(amps) if charging else 0,
        "charger_phases": None if dc or phases is None else int(phases),
        "fast_charger_present": bool(dc and charging),
        "fast_charger_brand": "<invalid>",
        "fast_charger_type": "Combo" if dc and charging else "<invalid>",
        "conn_charge_cable": "IEC" if (plug or charging) else "<invalid>",
        "time_to_full_charge": (ttf_min / 60.0) if (charging and ttf_min is not None) else 0.0,
        "minutes_to_full_charge": _int(ttf_min) if (charging and ttf_min is not None) else 0,
        "scheduled_charging_pending": False,
        "scheduled_charging_start_time": None,
        "battery_heater_on": False,
        "not_enough_power_to_heat": None,
        "trip_charging": False,
        "timestamp": ts_ms,
    }


def climate_state(cfg: Config, snap: Snapshot, ts_ms: int) -> dict[str, Any]:
    e = cfg.entity
    climate = e("climate")
    outside = _plausible_temp(_num(snap, cfg.outside_temp_entity)) if cfg.outside_temp_entity else None
    setpoint = _attr_num(snap, climate, "temperature")
    return {
        "inside_temp": _plausible_temp(_num(snap, e("inside_temp_c"))),
        "outside_temp": outside,
        "driver_temp_setting": setpoint,
        "passenger_temp_setting": setpoint,
        "is_climate_on": (_raw(snap, climate) or "off").lower() not in {"off"},
        "is_preconditioning": False,
        "fan_status": 0,
        "seat_heater_left": 0,
        "seat_heater_right": 0,
        "is_front_defroster_on": False,
        "is_rear_defroster_on": False,
        "timestamp": ts_ms,
    }


def drive_state(cfg: Config, snap: Snapshot, tracker: Tracker, shift: str | None, ts_ms: int) -> dict[str, Any]:
    e = cfg.entity
    fix = tracker.observe_fix(_fix(cfg, snap))
    speed_kmh = _num(snap, e("speed_kmh"))
    driving = shift == "D"
    return {
        "latitude": fix.lat if fix else None,
        "longitude": fix.lon if fix else None,
        "heading": _int(tracker.heading),
        # Tesla reports null speed when parked and TeslaMate treats a number as motion.
        "speed": kmh_to_mph(speed_kmh) if (driving and speed_kmh) else None,
        "shift_state": shift,
        "power": None,
        "timestamp": ts_ms,
        "gps_as_of": int(fix.at) if fix else None,
        "native_location_supported": 1,
        "native_latitude": fix.lat if fix else None,
        "native_longitude": fix.lon if fix else None,
        "native_type": "wgs",
    }


def vehicle_state(cfg: Config, snap: Snapshot, ts_ms: int) -> dict[str, Any]:
    e = cfg.entity
    locked = _raw(snap, e("lock"))
    return {
        "odometer": km_to_mi(_num(snap, e("odometer_km"))),
        "locked": (locked or "").lower() == "locked",
        "df": _door(snap, e("door_df")),
        "dr": _door(snap, e("door_dr")),
        "pf": _door(snap, e("door_pf")),
        "pr": _door(snap, e("door_pr")),
        "ft": _door(snap, e("hood")),
        "rt": _door(snap, e("trunk")),
        "car_version": CAR_VERSION,
        "vehicle_name": cfg.display_name,
        "sentry_mode": False,
        "is_user_present": False,
        "tpms_pressure_fl": psi_to_bar(_num(snap, e("tpms_fl_psi"))),
        "tpms_pressure_fr": psi_to_bar(_num(snap, e("tpms_fr_psi"))),
        "tpms_pressure_rl": psi_to_bar(_num(snap, e("tpms_rl_psi"))),
        "tpms_pressure_rr": psi_to_bar(_num(snap, e("tpms_rr_psi"))),
        "api_version": API_VERSION,
        "timestamp": ts_ms,
    }


def vehicle_config(ts_ms: int) -> dict[str, Any]:
    """Deliberately an UNRECOGNISED car_type. TeslaMate maps car_type -> model and
    anything it does not know becomes model = nil, which its UI renders as the plain
    display name rather than inventing a Tesla. Efficiency is derived empirically from
    charge data, so nothing here is load-bearing."""
    return {
        "car_type": "geely-ex2",
        "trim_badging": None,
        "exterior_color": None,
        "wheel_type": None,
        "spoiler_type": None,
        "has_air_suspension": False,
        "can_actuate_trunks": False,
        "car_special_type": "base",
        "timestamp": ts_ms,
    }


GUI_SETTINGS = {
    "gui_distance_units": "mi/hr",
    "gui_temperature_units": "C",
    "gui_charge_rate_units": "kW",
    "gui_24_hour_time": True,
    "gui_range_display": "Rated",
}


def build(cfg: Config, snap: Snapshot, tracker: Tracker, now: float) -> Bundle:
    """Everything the four endpoints need, computed once per poll."""
    e = cfg.entity
    tid = tesla_id(cfg.vin)
    reported_at = _epoch(_raw(snap, e("car_reported_at")))
    ts_ms = int((reported_at if reported_at is not None else now) * 1000)

    shift = shift_state(_raw(snap, e("engine_state")))
    chg = charge_state(cfg, snap, ts_ms)
    asleep = tracker.asleep(shift=shift, charging=chg["charging_state"], reported_at=reported_at, now=now)

    summary = vehicle_summary(cfg, tid, asleep)
    data = dict(summary)
    data.update(
        {
            "user_id": tid,
            "charge_state": chg,
            "climate_state": climate_state(cfg, snap, ts_ms),
            "drive_state": drive_state(cfg, snap, tracker, shift, ts_ms),
            "vehicle_state": vehicle_state(cfg, snap, ts_ms),
            "vehicle_config": vehicle_config(ts_ms),
            "gui_settings": {**GUI_SETTINGS, "timestamp": ts_ms},
        }
    )
    return Bundle(summary=summary, data=data, asleep=asleep, tesla_id=tid)
