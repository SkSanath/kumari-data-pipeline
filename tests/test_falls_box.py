"""Falls-box measurements on made-up arrays (no network)."""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sat"))
sys.path.insert(0, str(ROOT / "common"))
from falls_box import s1_metrics, s2_days, s2_metrics, to_measurements  # noqa: E402

INSIDE = np.zeros((10, 10), bool); INSIDE[2:8, 2:8] = True  # 36 px box


def test_s1_mean_in_linear_power_and_dark_share():
    vv = np.full((10, 10), -10.0); vv[2:5, 2:8] = -20.0  # half the box dark
    vh = np.full((10, 10), -17.0)
    m = s1_metrics(vv, vh, np.ones((10, 10), bool), INSIDE, -14, 0.8)
    assert m["quality"] == "ok" and m["dark_frac"] == 0.5 and m["vh_db"] == -17.0
    # mean of 0.1 and 0.01 (linear) = 0.055 -> -12.6 dB, not the -15 dB a mean of dB values would give
    assert m["vv_db"] == pytest.approx(-12.6, abs=0.05)


def test_s1_partial_pass_is_missing():
    valid = np.zeros((10, 10), bool); valid[:5] = True
    m = s1_metrics(np.full((10, 10), -10.0), np.full((10, 10), -17.0), valid, INSIDE, -14, 0.8)
    assert m["quality"] == "missing" and "vv_db" not in m


def bands(water_rows=3):
    b03 = np.full((10, 10), 0.05); b08 = np.full((10, 10), 0.30); b11 = np.full((10, 10), 0.20)  # vegetation / rock
    b03[2:2 + water_rows, 2:8] = 0.06; b08[2:2 + water_rows, 2:8] = 0.02; b11[2:2 + water_rows, 2:8] = 0.01  # water
    return b03, b08, b11


def test_s2_water_share_from_mndwi():
    b03, b08, b11 = bands(3)
    m = s2_metrics(b03, b08, b11, np.full((10, 10), 6.0), np.ones((10, 10)), INSIDE, [3, 8, 9, 10, 11], 0.8, 0.0, 0.0)
    assert m["quality"] == "ok" and m["water_frac"] == 0.5 and m["ndwi_frac"] == 0.5


def test_s2_cloudy_box_is_skipped_not_guessed():
    b03, b08, b11 = bands(3)
    scl = np.full((10, 10), 9.0); scl[7:] = 6  # cloud over most of the box
    m = s2_metrics(b03, b08, b11, scl, np.ones((10, 10)), INSIDE, [3, 8, 9, 10, 11], 0.8, 0.0, 0.0)
    assert m["quality"] == "missing" and "water_frac" not in m


def test_s2_tiles_of_one_day_are_one_acquisition():
    f = lambda t, i: {"id": i, "properties": {"datetime": t, "platform": "sentinel-2b", "eo:cloud_cover": 10}}  # noqa: E731
    days = s2_days([f("2031-01-05T05:20:00Z", "a"), f("2031-01-05T05:20:10Z", "b"), f("2031-01-10T05:20:00Z", "c")])
    assert [len(d["ids"]) for d in days] == [2, 1]


def test_rows_become_public_signals_with_box_status():
    r = {"acquired_at": "2031-01-05T05:20:00Z", "sensor": "s2", "quality": "ok", "why": "", "water_frac": "0.4", "ndwi_frac": "0.3",
         "valid_fraction": "0.95", "box_px": "36", "product_ids": "S2B_X", "box_status": "draft", "processed_at": "2031-01-06T00:00:00Z"}
    ms = to_measurements(r)
    assert {m["signal"] for m in ms} == {"falls_s2_water_frac", "falls_s2_ndwi_frac"}
    assert all(m["visibility"] == "public" and m["meta"]["box_status"] == "draft" for m in ms)
    miss = to_measurements({**r, "quality": "missing", "water_frac": "", "ndwi_frac": ""})
    assert all(m["value"] is None and m["quality"] == "missing" for m in miss)
