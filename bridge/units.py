"""Unit conversions. Pure: no I/O, no Home Assistant, no clock.

The Tesla Owner API speaks MILES and MPH. TeslaMate converts back to km on ingest
(Convert.miles_to_km/2 and Convert.mph_to_kmh/1). Feed it km and every distance is
silently inflated by 1.609 -- and it will look plausible. This is the single most
likely quiet failure in the whole project, which is why these five lines are a module
of their own with a golden test pinned to a real number.

KM_FACTOR is TeslaMate's own constant (lib/teslamate/convert.ex) rather than a rounded
0.621371, so the km -> mi -> km round trip is lossless at their precision.
"""

from __future__ import annotations

KM_FACTOR = 0.62137119223733
PSI_PER_BAR = 14.5037738


def km_to_mi(km: float | None) -> float | None:
    return None if km is None else km * KM_FACTOR


def mi_to_km(mi: float | None) -> float | None:
    return None if mi is None else mi / KM_FACTOR


def kmh_to_mph(kmh: float | None) -> float | None:
    # Same factor: a distance rate converts exactly like a distance.
    return km_to_mi(kmh)


def psi_to_bar(psi: float | None) -> float | None:
    """Tesla's tpms_pressure_* fields are bar; the car reports psi."""
    return None if psi is None else psi / PSI_PER_BAR
