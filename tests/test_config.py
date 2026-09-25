"""The options file and the environment are the only inputs; neither is required."""

import json

from bridge import config


def test_defaults_without_an_options_file(tmp_path):
    cfg = config.load(str(tmp_path / "missing.json"), env={})
    assert cfg.car_entity_prefix == "geely_e22h_gp_0137"
    assert cfg.entity("soc") == "sensor.geely_e22h_gp_0137_battery"
    assert cfg.entity("location") == "device_tracker.geely_e22h_gp_0137_location"
    assert len(cfg.vin) == 17
    assert cfg.outside_temp_entity == ""


def test_options_file_overrides_and_list_shaped_entity_overrides(tmp_path):
    f = tmp_path / "options.json"
    f.write_text(json.dumps({
        "display_name": "Car",
        "car_entity_prefix": "othercar",
        "max_plausible_kmh": 120,
        "outside_temp_entity": "sensor.local_weather",
        "entity_overrides": [{"role": "odometer_km", "entity_id": "sensor.custom_odo"}],
        "log_level": "DEBUG",
    }), encoding="utf-8")
    cfg = config.load(str(f), env={})
    assert cfg.display_name == "Car"
    assert cfg.entity("soc") == "sensor.othercar_battery"
    assert cfg.entity("odometer_km") == "sensor.custom_odo"
    assert cfg.max_plausible_kmh == 120.0
    assert cfg.outside_temp_entity == "sensor.local_weather"
    assert cfg.log_level == "debug"


def test_ha_url_and_token_fall_back_to_env(tmp_path):
    cfg = config.load(str(tmp_path / "none.json"), env={"HA_URL": "http://x:8123", "HA_TOKEN": "t"})
    assert cfg.ha_url == "http://x:8123" and cfg.ha_token == "t"


def test_every_role_resolves_to_an_entity_id():
    cfg = config.Config()
    for role, eid in cfg.entities.items():
        assert "." in eid, role
