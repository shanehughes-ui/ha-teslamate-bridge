"""Units. The most likely silent failure in the project, so pinned to real numbers."""

from bridge import units


def test_golden_100km_is_62_137_miles():
    # A 100 km odometer must read 62.137 miles to TeslaMate, or every distance it
    # stores is 1.609x wrong and looks plausible.
    assert round(units.km_to_mi(100.0), 3) == 62.137


def test_round_trip_is_lossless_at_teslamate_precision():
    for km in (0.0, 1.0, 4188.0, 123456.789):
        assert abs(units.mi_to_km(units.km_to_mi(km)) - km) < 1e-9


def test_kmh_converts_like_km():
    assert units.kmh_to_mph(100.0) == units.km_to_mi(100.0)


def test_psi_to_bar():
    assert round(units.psi_to_bar(14.5037738), 6) == 1.0
    assert round(units.psi_to_bar(38.0), 3) == 2.62


def test_none_passes_through():
    assert units.km_to_mi(None) is None
    assert units.kmh_to_mph(None) is None
    assert units.psi_to_bar(None) is None
    assert units.mi_to_km(None) is None
