"""Outlier rejection, heading, gear, charging and sleep -- against the measured cases.

Coordinates here are synthetic offsets from the equator/prime meridian. One degree of
latitude is ~111,195 m on the sphere used, which is what the offsets below are built
from. Nothing in this file is a real place.
"""

from bridge.sanitise import Fix, Tracker, accept_fix, bearing_deg, charging_state, haversine_m, heading, shift_state

M_PER_DEG_LAT = 111_195.0


def _north(metres: float, at: float) -> Fix:
    return Fix(lat=metres / M_PER_DEG_LAT, lon=0.0, at=at)


# --- outlier rejection --------------------------------------------------------------


def test_the_real_7km_hop_in_31s_is_rejected():
    # Max observed hop on the real car: 7.2 km in one 31 s step, an implied 839 km/h,
    # because the upstream position lags then catches up.
    ok, reason = accept_fix(Fix(0.0, 0.0, 0.0), _north(7200.0, 31.0))
    assert ok is False
    assert "km/h" in reason


def test_the_p90_550m_hop_in_31s_is_kept():
    ok, _ = accept_fix(Fix(0.0, 0.0, 0.0), _north(550.0, 31.0))
    assert ok is True


def test_first_fix_is_always_accepted():
    assert accept_fix(None, _north(0.0, 5.0)) == (True, "first fix")


def test_freshness_gate_rejects_a_fix_that_has_not_advanced():
    prev = _north(100.0, 60.0)
    assert accept_fix(prev, _north(200.0, 60.0))[0] is False   # same timestamp
    assert accept_fix(prev, _north(200.0, 30.0))[0] is False   # older


def test_stationary_but_newer_is_accepted():
    prev = _north(100.0, 60.0)
    ok, reason = accept_fix(prev, Fix(prev.lat, prev.lon, 91.0))
    assert ok is True and reason == "stationary"


def test_haversine_is_sane():
    assert round(haversine_m(0.0, 0.0, 1.0 / M_PER_DEG_LAT * 1000.0, 0.0)) == 1000


# --- heading ------------------------------------------------------------------------


def test_two_fixes_due_north_give_zero():
    assert round(heading(Fix(0.0, 0.0, 0.0), Fix(0.01, 0.0, 31.0))) == 0


def test_two_fixes_due_east_give_ninety():
    assert round(heading(Fix(0.0, 0.0, 0.0), Fix(0.0, 0.01, 31.0))) == 90


def test_single_fix_gives_null_heading():
    assert heading(None, Fix(0.0, 0.0, 0.0)) is None
    assert heading(Fix(0.0, 0.0, 0.0), None) is None


def test_same_point_gives_null_heading():
    assert heading(Fix(0.01, 0.02, 0.0), Fix(0.01, 0.02, 31.0)) is None


def test_bearing_range():
    assert 0.0 <= bearing_deg(0.0, 0.0, -0.01, -0.01) < 360.0


# --- gear ---------------------------------------------------------------------------


def test_shift_state_from_engine():
    assert shift_state("engine-running") == "D"
    assert shift_state("ENGINE-RUNNING") == "D"
    assert shift_state("engine-off") == "P"
    assert shift_state("unknown-thing") is None
    assert shift_state(None) is None


# --- charging -----------------------------------------------------------------------


def test_charging_state_mapping():
    assert charging_state("Charging", True) == "Charging"
    assert charging_state("Connected", True) == "Stopped"
    assert charging_state("Plugged in", None) == "Stopped"
    assert charging_state("Disconnected", False) == "Disconnected"
    assert charging_state(None, None) == "Disconnected"
    # Plug on but no connection word: cable in, not charging.
    assert charging_state("Disconnected", True) == "Stopped"


# --- sleep --------------------------------------------------------------------------


def test_stale_report_is_asleep_whatever_it_says():
    t = Tracker(stale_after_s=2700.0)
    assert t.asleep(shift="D", charging="Charging", reported_at=1000.0, now=1000.0 + 2701.0) is True


def test_missing_report_time_is_asleep():
    assert Tracker().asleep(shift="D", charging="Charging", reported_at=None, now=5.0) is True


def test_driving_fresh_is_awake():
    assert Tracker().asleep(shift="D", charging="Disconnected", reported_at=1000.0, now=1010.0) is False


def test_charging_parked_is_awake():
    assert Tracker().asleep(shift="P", charging="Charging", reported_at=1000.0, now=1010.0) is False


def test_parked_unplugged_with_no_recent_activity_is_asleep():
    assert Tracker().asleep(shift="P", charging="Disconnected", reported_at=1000.0, now=1010.0) is True


def test_grace_keeps_the_car_awake_just_after_a_drive_ends():
    t = Tracker(grace_s=300.0)
    assert t.asleep(shift="D", charging="Disconnected", reported_at=1000.0, now=1000.0) is False
    # Engine off 60 s later: still awake so TeslaMate sees the P that closes the drive.
    assert t.asleep(shift="P", charging="Disconnected", reported_at=1060.0, now=1060.0) is False
    # Well past the grace: asleep.
    assert t.asleep(shift="P", charging="Disconnected", reported_at=1400.0, now=1400.0) is True


# --- tracker ------------------------------------------------------------------------


def test_tracker_holds_the_last_good_fix_through_an_outlier_and_derives_heading():
    t = Tracker()
    a = t.observe_fix(Fix(0.0, 0.0, 0.0))
    assert a == Fix(0.0, 0.0, 0.0) and t.heading is None
    b = t.observe_fix(_north(500.0, 31.0))
    assert b.lat > 0 and round(t.heading) == 0
    # The 7.2 km spike: rejected, last good fix and heading unchanged.
    c = t.observe_fix(_north(7700.0, 62.0))
    assert c == b and round(t.heading) == 0 and "km/h" in t.last_reason
    # Nothing offered: keeps what it had.
    assert t.observe_fix(None) == b
