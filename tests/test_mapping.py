"""A recorded-shape Home Assistant snapshot maps to a vehicle_data payload that carries
the key set TeslaMate expects, in miles, with absent signals absent rather than zero.

The snapshot mirrors the entity ids and attribute names the source integration
publishes, with synthetic values. No real coordinates and no real places.
"""

from __future__ import annotations

from bridge import mapping
from bridge.config import Config
from bridge.sanitise import Tracker
from bridge.units import KM_FACTOR

P = "testcar"
CFG = Config(car_entity_prefix=P, display_name="Test Car", vin="TESTBRIDGE0000001")
REPORTED = "2026-09-25T06:13:30+00:00"
REPORTED_EPOCH = 1_790_316_810.0  # the ISO above, as epoch seconds
NOW = REPORTED_EPOCH + 10.0


def _e(state, attributes=None, last_updated=REPORTED):
    return {"state": state, "attributes": attributes or {}, "last_updated": last_updated}


def snapshot(**over):
    s = {
        f"sensor.{P}_battery": _e("62.4"),
        f"sensor.{P}_total_mileage": _e("4188.0"),
        f"sensor.{P}_electric_range": _e("181"),
        f"sensor.{P}_speed": _e("0.0"),
        f"sensor.{P}_engine_state": _e("engine-off"),
        f"sensor.{P}_car_reported_at": _e(REPORTED),
        f"sensor.{P}_charger_connection": _e("Charging"),
        f"binary_sensor.{P}_charger_plug": _e("on"),
        f"sensor.{P}_charge_current": _e("7.2"),
        f"sensor.{P}_charge_voltage": _e("238.3"),
        f"sensor.{P}_charging_power": _e("1.72", {"phases": 1, "supply": "AC"}),
        f"sensor.{P}_dc_charge_current": _e("unknown"),
        f"sensor.{P}_dc_charge_voltage": _e("unknown"),
        f"sensor.{P}_time_to_full_charge": _e("772.0"),
        f"sensor.{P}_interior_temperature": _e("42.7"),
        f"climate.{P}_climate": _e("off", {"temperature": 15.5}),
        f"lock.{P}_doors": _e("locked"),
        f"binary_sensor.{P}_door_driver": _e("off"),
        f"binary_sensor.{P}_door_passenger": _e("off"),
        f"binary_sensor.{P}_door_rear_left": _e("off"),
        f"binary_sensor.{P}_door_rear_right": _e("on"),
        f"binary_sensor.{P}_hood": _e("off"),
        f"binary_sensor.{P}_trunk": _e("off"),
        f"sensor.{P}_tire_pressure_fl": _e("38.0"),
        f"sensor.{P}_tire_pressure_fr": _e("37.6"),
        f"sensor.{P}_tire_pressure_rl": _e("37.0"),
        f"sensor.{P}_tire_pressure_rr": _e("unknown"),
        f"device_tracker.{P}_location": _e("home", {"latitude": 0.0123, "longitude": 0.0456, "gps_accuracy": 0}),
        "sensor.ev_stats_session_energy": _e("6.322"),
    }
    s.update(over)
    return s


EXPECTED_TOP = {
    "id", "vehicle_id", "vin", "display_name", "state", "in_service", "id_s", "api_version",
    "user_id", "charge_state", "climate_state", "drive_state", "vehicle_state", "vehicle_config", "gui_settings",
}
EXPECTED_CHARGE = {
    "battery_level", "usable_battery_level", "battery_range", "est_battery_range", "ideal_battery_range",
    "charging_state", "charger_power", "charge_energy_added", "charge_limit_soc", "charge_port_door_open",
    "charger_voltage", "charger_actual_current", "charger_phases", "fast_charger_present", "fast_charger_type",
    "conn_charge_cable", "time_to_full_charge", "minutes_to_full_charge", "timestamp",
}
EXPECTED_DRIVE = {
    "latitude", "longitude", "heading", "speed", "shift_state", "power", "timestamp", "gps_as_of",
    "native_latitude", "native_longitude", "native_type", "native_location_supported",
}
EXPECTED_VEHICLE = {
    "odometer", "locked", "df", "dr", "pf", "pr", "ft", "rt", "car_version", "vehicle_name",
    "sentry_mode", "is_user_present", "tpms_pressure_fl", "tpms_pressure_fr", "tpms_pressure_rl",
    "tpms_pressure_rr", "timestamp",
}


def test_payload_carries_the_expected_key_sets():
    b = mapping.build(CFG, snapshot(), Tracker(), NOW)
    d = b.data
    assert EXPECTED_TOP <= set(d)
    assert EXPECTED_CHARGE <= set(d["charge_state"])
    assert EXPECTED_DRIVE <= set(d["drive_state"])
    assert EXPECTED_VEHICLE <= set(d["vehicle_state"])
    assert d["gui_settings"]["gui_distance_units"] == "mi/hr"
    # products must carry vehicle_id or TeslaMate never sees the car
    assert b.summary["vehicle_id"] == b.summary["id"] == b.tesla_id
    assert b.summary["vin"] == "TESTBRIDGE0000001"


def test_tesla_id_is_stable_and_derived_from_the_vin():
    assert mapping.tesla_id("TESTBRIDGE0000001") == mapping.tesla_id("TESTBRIDGE0000001")
    assert mapping.tesla_id("TESTBRIDGE0000001") != mapping.tesla_id("TESTBRIDGE0000002")


def test_distances_are_emitted_in_miles():
    d = mapping.build(CFG, snapshot(), Tracker(), NOW).data
    assert abs(d["vehicle_state"]["odometer"] - 4188.0 * KM_FACTOR) < 1e-9
    assert abs(d["charge_state"]["battery_range"] - 181.0 * KM_FACTOR) < 1e-9


def test_charging_session_maps_and_energy_comes_from_the_stats_system():
    d = mapping.build(CFG, snapshot(), Tracker(), NOW).data
    cs = d["charge_state"]
    assert cs["charging_state"] == "Charging"
    assert cs["battery_level"] == 62
    assert cs["charge_energy_added"] == 6.322          # read, not recomputed
    assert cs["charger_phases"] == 1
    assert cs["charger_actual_current"] == 7
    assert cs["charger_voltage"] == 238
    assert cs["charger_power"] == 2
    assert cs["fast_charger_present"] is False
    assert cs["conn_charge_cable"] == "IEC"
    assert cs["charge_port_door_open"] is True
    assert cs["minutes_to_full_charge"] == 772
    assert cs["charge_limit_soc"] is None              # the car does not report it


def test_charging_car_is_awake_and_parked_car_is_asleep():
    awake = mapping.build(CFG, snapshot(), Tracker(), NOW)
    assert awake.asleep is False and awake.summary["state"] == "online"
    parked = snapshot(**{
        f"sensor.{P}_charger_connection": _e("Disconnected"),
        f"binary_sensor.{P}_charger_plug": _e("off"),
    })
    asleep = mapping.build(CFG, parked, Tracker(), NOW)
    assert asleep.asleep is True and asleep.summary["state"] == "asleep"
    assert asleep.data["charge_state"]["charging_state"] == "Disconnected"
    assert asleep.data["charge_state"]["charge_energy_added"] is None


def test_shift_state_is_synthesised_from_the_engine():
    parked = mapping.build(CFG, snapshot(), Tracker(), NOW).data
    assert parked["drive_state"]["shift_state"] == "P"
    assert parked["drive_state"]["speed"] is None
    driving = snapshot(**{f"sensor.{P}_engine_state": _e("engine-running"), f"sensor.{P}_speed": _e("50.0")})
    d = mapping.build(CFG, driving, Tracker(), NOW).data
    assert d["drive_state"]["shift_state"] == "D"
    assert abs(d["drive_state"]["speed"] - 50.0 * KM_FACTOR) < 1e-9


def test_absent_signals_are_absent_not_zero():
    d = mapping.build(CFG, snapshot(), Tracker(), NOW).data
    assert d["climate_state"]["outside_temp"] is None       # the car reports none
    assert d["vehicle_state"]["tpms_pressure_rr"] is None    # 'unknown' in the snapshot
    assert d["drive_state"]["heading"] is None               # single fix
    assert d["drive_state"]["power"] is None
    assert d["vehicle_state"]["car_version"] == mapping.CAR_VERSION


def test_outside_temp_comes_from_a_configured_entity_when_named():
    cfg = Config(car_entity_prefix=P, outside_temp_entity="sensor.some_local_weather")
    snap = snapshot(**{"sensor.some_local_weather": _e("18.4")})
    d = mapping.build(cfg, snap, Tracker(), NOW).data
    assert d["climate_state"]["outside_temp"] == 18.4


def test_position_and_doors_and_tyres():
    d = mapping.build(CFG, snapshot(), Tracker(), NOW).data
    ds, vs = d["drive_state"], d["vehicle_state"]
    assert ds["latitude"] == 0.0123 and ds["longitude"] == 0.0456
    assert ds["gps_as_of"] == int(REPORTED_EPOCH)
    assert vs["locked"] is True
    assert (vs["df"], vs["pf"], vs["dr"], vs["pr"], vs["ft"], vs["rt"]) == (0, 0, 0, 1, 0, 0)
    assert round(vs["tpms_pressure_fl"], 2) == 2.62


def test_timestamps_come_from_the_car_report_time():
    d = mapping.build(CFG, snapshot(), Tracker(), NOW).data
    assert d["charge_state"]["timestamp"] == int(REPORTED_EPOCH * 1000)


def test_missing_location_entity_yields_no_position_not_a_crash():
    snap = snapshot()
    del snap[f"device_tracker.{P}_location"]
    d = mapping.build(CFG, snap, Tracker(), NOW).data
    assert d["drive_state"]["latitude"] is None and d["drive_state"]["gps_as_of"] is None


def test_entity_override_is_honoured():
    cfg = Config(car_entity_prefix=P, entity_overrides={"soc": "sensor.other_soc"})
    snap = snapshot(**{"sensor.other_soc": _e("41.0")})
    d = mapping.build(cfg, snap, Tracker(), NOW).data
    assert d["charge_state"]["battery_level"] == 41
