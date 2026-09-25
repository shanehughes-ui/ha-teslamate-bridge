"""Outlier rejection, the freshness gate, heading, gear and sleep. Pure.

Everything here is a judgement TeslaMate cannot make for itself because it assumes
Tesla-quality telemetry. Measured on the real car on 2026-09-25, so none of it is
guessed:

  * GPS arrives every ~31 s while moving; parked jitter is median 0.4 m. Good fixes.
  * The moving hop is median 404 m, p90 550 m -- and ONE hop was 7.2 km in a single
    31 s step, an implied 839 km/h, because the upstream position lags and then catches
    up. Without rejection that draws a spike through the route and writes a nonsense
    speed_max that TeslaMate keeps forever.
  * gps_accuracy is 0 on every one of 2200 sampled fixes, and it is not a hardcode to
    fix: the integration never implements location_accuracy, so Home Assistant's
    base-class default is what gets published. The field carries no information, so
    nothing here reads it. Plausibility and freshness are the only gates available.
  * GPS was frozen server-side for weeks and resumed with no warning. Never assume a fix
    is live; a fix only counts when its timestamp advances.
  * The car reports no gear and no heading. Gear is synthesised from the engine state
    (the same signal ev_stats uses for trip detection); heading is derived from
    consecutive accepted fixes.
  * Geely polls every 30 s when driving or charging and backs off to ~15 min parked.
    A parked, unplugged car should read ASLEEP to TeslaMate -- otherwise it invents a
    ten-hour stationary drive.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_RADIUS_M = 6_371_008.8
MAX_PLAUSIBLE_KMH = 150.0

# The engine-state strings the source integration emits. Matched case-insensitively.
ENGINE_RUNNING = frozenset(
    {"engine-running", "engine_running", "engine-on", "engine_on", "engine on", "running"}
)
ENGINE_OFF = frozenset({"engine-off", "engine_off", "engine off", "off", "stopped"})

# Charger-connection strings that mean "cable in, not charging".
PLUGGED_NOT_CHARGING = frozenset({"connected", "plugged in", "plugged-in", "plugged_in"})


@dataclass(frozen=True)
class Fix:
    """One GPS fix. `at` is epoch seconds of when it was REPORTED, not received."""

    lat: float
    lon: float
    at: float


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from point 1 to point 2, 0..360, 0 = north, 90 = east."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def implied_kmh(prev: Fix, new: Fix) -> float | None:
    dt = new.at - prev.at
    if dt <= 0:
        return None
    return haversine_m(prev.lat, prev.lon, new.lat, new.lon) / dt * 3.6


def accept_fix(prev: Fix | None, new: Fix, max_kmh: float = MAX_PLAUSIBLE_KMH) -> tuple[bool, str]:
    """Should this fix replace the last accepted one?

    Returns (accepted, reason). The reason is for the log, so a rejected route point
    can be explained after the fact instead of merely being missing.
    """
    if prev is None:
        return True, "first fix"
    if new.at <= prev.at:
        # The freshness gate: the same (or an older) fix served again is not a new
        # position, however often the integration re-publishes it.
        return False, "fix has not advanced"
    if new.lat == prev.lat and new.lon == prev.lon:
        return True, "stationary"
    v = implied_kmh(prev, new)
    if v is not None and v > max_kmh:
        return False, f"implied {v:.0f} km/h exceeds {max_kmh:.0f}"
    return True, "ok"


def heading(prev: Fix | None, new: Fix | None) -> float | None:
    """Bearing between two accepted fixes; None when there is nothing to derive it from."""
    if prev is None or new is None:
        return None
    if (prev.lat, prev.lon) == (new.lat, new.lon):
        return None
    return bearing_deg(prev.lat, prev.lon, new.lat, new.lon)


def shift_state(engine_state: str | None) -> str | None:
    """The car reports no gear. TeslaMate gates the :driving transition on
    shift_state in D/N/R, so synthesise it from the engine: running -> D, off -> P,
    anything else -> None (absent, never a guess)."""
    if engine_state is None:
        return None
    s = engine_state.strip().lower()
    if s in ENGINE_RUNNING:
        return "D"
    if s in ENGINE_OFF:
        return "P"
    return None


def charging_state(charger_connection: str | None, charger_plug: bool | None) -> str:
    """Tesla's charging_state from the two signals this platform actually carries.

    *_charging_reported / *_plugged_in_reported are permanently off on this car, so
    they are deliberately not inputs here. TeslaMate opens a charge on "Charging" and
    closes it on anything else, so only that word is load-bearing.
    """
    s = (charger_connection or "").strip().lower()
    if s == "charging":
        return "Charging"
    if s in PLUGGED_NOT_CHARGING or charger_plug:
        return "Stopped"
    return "Disconnected"


@dataclass
class Tracker:
    """The little state a translator has to carry between polls. Pure, but stateful:
    the caller supplies `now`, it never reads a clock."""

    max_kmh: float = MAX_PLAUSIBLE_KMH
    stale_after_s: float = 2700.0
    grace_s: float = 300.0
    last: Fix | None = None
    heading: float | None = None
    last_active: float | None = None
    last_reason: str = ""

    def observe_fix(self, new: Fix | None) -> Fix | None:
        """Offer a fix; get back the fix TeslaMate should be told about."""
        if new is None:
            return self.last
        ok, reason = accept_fix(self.last, new, self.max_kmh)
        self.last_reason = reason
        if not ok:
            return self.last
        h = heading(self.last, new)
        if h is not None:
            self.heading = h
        self.last = new
        return self.last

    def asleep(self, *, shift: str | None, charging: str, reported_at: float | None, now: float) -> bool:
        """Should TeslaMate be told the car is asleep?

        Asleep when the data itself is stale (the integration has gone quiet, whatever
        the last values said), and when the car is parked and not charging. Driving or
        charging is awake. After either stops, stay awake for a short grace period so
        TeslaMate sees the "P" / "Stopped" poll that closes its drive or charge cleanly
        rather than having it cut off by a 408.
        """
        if reported_at is None or (now - reported_at) > self.stale_after_s:
            return True
        if shift == "D" or charging == "Charging":
            self.last_active = now
            return False
        if self.last_active is not None and (now - self.last_active) < self.grace_s:
            return False
        return True
