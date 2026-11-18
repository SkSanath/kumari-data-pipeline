import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
from measurement import measurement  # noqa: E402

T = datetime(2026, 11, 14, 0, 47, tzinfo=timezone.utc)
BASE = dict(signal="reservoir_area_km2", place="pechiparai", value=12.34, unit="km2", observed_at=T,
            source="Copernicus Sentinel-1 GRD", source_url="https://dataspace.copernicus.eu/",
            quality="ok", visibility="public", fetched_at=T + timedelta(days=1))


def test_spec_example():
    m = measurement(**BASE, meta={"orbit": "DESCENDING", "rel_orbit": 63})
    assert m["observed_at"] == "2026-11-14T00:47:00Z"
    assert m["meta"]["rel_orbit"] == 63


def test_missing_is_missing():
    assert measurement(**{**BASE, "value": None, "quality": "missing"})["value"] is None
    with pytest.raises(ValueError):
        measurement(**{**BASE, "value": None})
    with pytest.raises(ValueError):
        measurement(**{**BASE, "quality": "missing"})


def test_needs_zone_and_source():
    with pytest.raises(ValueError):
        measurement(**{**BASE, "observed_at": datetime(2026, 11, 14)})
    with pytest.raises(ValueError):
        measurement(**{**BASE, "source_url": ""})


def test_no_future_observation():
    with pytest.raises(ValueError):
        measurement(**{**BASE, "observed_at": T + timedelta(days=2)})


def test_private_allowed():
    assert measurement(**{**BASE, "visibility": "private"})["visibility"] == "private"
