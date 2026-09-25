"""Where the bridge finds things. Reads the add-on options file and the environment
and nothing else -- no Home Assistant import, no network.

Every entity id is derived from one prefix so a different car is one option away,
and any single role can still be overridden for the odd entity that does not follow
the pattern. The defaults are the live ids on the instance this was built against.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

OPTIONS_FILE = os.environ.get("BRIDGE_OPTIONS_FILE", "/data/options.json")

# role -> entity id template. {p} is the car entity prefix.
ROLE_TEMPLATES: dict[str, str] = {
    "soc": "sensor.{p}_battery",
    "odometer_km": "sensor.{p}_total_mileage",
    "range_km": "sensor.{p}_electric_range",
    "speed_kmh": "sensor.{p}_speed",
    "engine_state": "sensor.{p}_engine_state",
    "car_reported_at": "sensor.{p}_car_reported_at",
    "charger_connection": "sensor.{p}_charger_connection",
    "charger_plug": "binary_sensor.{p}_charger_plug",
    "charge_current_a": "sensor.{p}_charge_current",
    "charge_voltage_v": "sensor.{p}_charge_voltage",
    "charging_power_kw": "sensor.{p}_charging_power",
    "dc_charge_current_a": "sensor.{p}_dc_charge_current",
    "dc_charge_voltage_v": "sensor.{p}_dc_charge_voltage",
    "time_to_full_min": "sensor.{p}_time_to_full_charge",
    "inside_temp_c": "sensor.{p}_interior_temperature",
    "climate": "climate.{p}_climate",
    "lock": "lock.{p}_doors",
    "door_df": "binary_sensor.{p}_door_driver",
    "door_pf": "binary_sensor.{p}_door_passenger",
    "door_dr": "binary_sensor.{p}_door_rear_left",
    "door_pr": "binary_sensor.{p}_door_rear_right",
    "hood": "binary_sensor.{p}_hood",
    "trunk": "binary_sensor.{p}_trunk",
    "tpms_fl_psi": "sensor.{p}_tire_pressure_fl",
    "tpms_fr_psi": "sensor.{p}_tire_pressure_fr",
    "tpms_rl_psi": "sensor.{p}_tire_pressure_rl",
    "tpms_rr_psi": "sensor.{p}_tire_pressure_rr",
    "location": "device_tracker.{p}_location",
}


@dataclass(frozen=True)
class Config:
    display_name: str = "Geely EX2"
    # Synthetic. cars.vin is NOT NULL in TeslaMate (migration 20260715081000); any
    # unique 17-character string works and it need not be a real VIN. Changing it after
    # first run would register a NEW car and orphan the history, so treat it as fixed.
    vin: str = "GEELYEX2HABRIDGE1"
    car_entity_prefix: str = "geely_e22h_gp_0137"
    # Session energy is read from the stats system that already tracks it -- never
    # recomputed here. Primary is the public integration, fallback the YAML package.
    session_energy_entity: str = "sensor.ev_stats_session_energy"
    session_energy_fallback_entity: str = "sensor.ev_session_energy"
    # The car reports no outside temperature (empty string, no numeric history). If a
    # local weather entity is named here it is used and the README says so; otherwise
    # the field is absent. Never a fabricated number.
    outside_temp_entity: str = ""
    max_plausible_kmh: float = 150.0
    stale_after_seconds: int = 2700
    online_grace_seconds: int = 300
    cache_ttl_seconds: float = 5.0
    log_level: str = "info"
    # Only used when NOT running under the Supervisor (local dev / tests). Under the
    # Supervisor the bridge uses SUPERVISOR_TOKEN and http://supervisor/core/api and
    # no long-lived token is stored anywhere.
    ha_url: str = ""
    ha_token: str = ""
    entity_overrides: dict[str, str] = field(default_factory=dict)

    def entity(self, role: str) -> str:
        if role in self.entity_overrides:
            return self.entity_overrides[role]
        return ROLE_TEMPLATES[role].format(p=self.car_entity_prefix)

    @property
    def entities(self) -> dict[str, str]:
        return {role: self.entity(role) for role in ROLE_TEMPLATES}


def _overrides(raw: Any) -> dict[str, str]:
    """Accept the add-on's list-of-{role, entity_id} shape or a plain dict."""
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(k, str) and isinstance(v, str) and v:
                out[k] = v
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                role, eid = item.get("role"), item.get("entity_id")
                if isinstance(role, str) and isinstance(eid, str) and eid:
                    out[role] = eid
    return out


def load(options_file: str = OPTIONS_FILE, env: dict[str, str] | None = None) -> Config:
    env = dict(os.environ) if env is None else env
    raw: dict[str, Any] = {}
    try:
        with open(options_file, encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            raw = loaded
    except (OSError, ValueError):
        raw = {}

    def pick(name: str, default: Any) -> Any:
        v = raw.get(name)
        return default if v is None or v == "" and not isinstance(default, str) else v

    return Config(
        display_name=str(pick("display_name", "Geely EX2")),
        vin=str(pick("vin", "GEELYEX2HABRIDGE1")),
        car_entity_prefix=str(pick("car_entity_prefix", "geely_e22h_gp_0137")),
        session_energy_entity=str(raw.get("session_energy_entity") or "sensor.ev_stats_session_energy"),
        session_energy_fallback_entity=str(raw.get("session_energy_fallback_entity") or "sensor.ev_session_energy"),
        outside_temp_entity=str(raw.get("outside_temp_entity") or ""),
        max_plausible_kmh=float(pick("max_plausible_kmh", 150.0)),
        stale_after_seconds=int(pick("stale_after_seconds", 2700)),
        online_grace_seconds=int(pick("online_grace_seconds", 300)),
        cache_ttl_seconds=float(pick("cache_ttl_seconds", 5.0)),
        log_level=str(pick("log_level", env.get("LOG_LEVEL", "info"))).lower(),
        ha_url=str(raw.get("ha_url") or env.get("HA_URL", "")),
        ha_token=str(raw.get("ha_token") or env.get("HA_TOKEN", "")),
        entity_overrides=_overrides(raw.get("entity_overrides")),
    )
