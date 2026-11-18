"""Reservoir area maths on MADE-UP dB rasters (no satellite data)."""
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("skimage")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sat"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
import s1_reservoir as s1  # noqa: E402

S = s1.Settings(clamp_min=-22, clamp_max=-14, min_separation=3, min_valid=0.98, max_jump_pct=15, res_m=10, outlines={})
rng = np.random.default_rng(1)


def scene(water_rows=40, h=100, w=100, water_db=-23.0, land_db=-8.0, noise=1.0):
    db = np.full((h, w), land_db, "float32") + rng.normal(0, noise, (h, w)).astype("float32")
    db[:water_rows] = water_db + rng.normal(0, noise, (water_rows, w))
    return db, np.ones((h, w), bool), np.ones((h, w), bool)


def test_bimodal_scene_gives_the_water_area():
    db, valid, inside = scene(water_rows=40)
    m = s1.measure(db, valid, inside, 10, S)
    assert m["quality"] == "ok"
    assert m["water_px"] == pytest.approx(4000, abs=20)
    assert m["area_km2"] == pytest.approx(0.4, abs=0.002)
    assert -20 < m["threshold_db"] < -10


def test_flat_scene_is_poor():
    db = np.full((100, 100), -12.0, "float32") + rng.normal(0, 0.5, (100, 100)).astype("float32")
    m = s1.measure(db, np.ones_like(db, bool), np.ones_like(db, bool), 10, S)
    assert m["quality"] == "poor"


def test_partial_pass_is_missing_not_guessed():
    db, valid, inside = scene()
    valid[:, :5] = False  # 5% of the outline not covered
    m = s1.measure(db, valid, inside, 10, S)
    assert m["quality"] == "missing" and m["area_km2"] is None


def test_only_pixels_inside_the_outline_count():
    db, valid, _ = scene(water_rows=40)
    inside = np.zeros_like(valid)
    inside[:, :50] = True
    m = s1.measure(db, valid, inside, 10, S)
    assert m["water_px"] == pytest.approx(2000, abs=15)


def test_clamped_threshold_is_suspect():
    db, valid, inside = scene(water_rows=50, water_db=-35, land_db=-28)  # Otsu lands below -22
    m = s1.measure(db, valid, inside, 10, S)
    assert m["quality"] == "suspect" and m["threshold_db"] == -22


def test_jump_check():
    row = {"quality": "ok", "area_km2": 10.0, "why": ""}
    assert s1.jump_check(row, {"area_km2": "9.5"}, 15)["quality"] == "ok"
    j = s1.jump_check(row, {"area_km2": "12.5"}, 15)
    assert j["quality"] == "suspect" and "-20%" in j["why"]
    assert s1.jump_check(row, None, 15)["quality"] == "ok"


def test_slices_of_one_pass_are_grouped_and_ascending_dropped():
    f = lambda id_, t, orbit="descending": {"id": id_, "properties": {"datetime": t, "sat:orbit_state": orbit, "sat:relative_orbit": 165, "platform": "sentinel-1a", "sar:instrument_mode": "IW"}}
    passes = s1.group_passes([f("a", "2026-11-02T00:40:22Z"), f("b", "2026-11-02T00:40:47Z"), f("c", "2026-11-14T00:40:47Z"), f("d", "2026-11-10T12:00:00Z", "ascending")])
    assert [p["ids"] for p in passes] == [["a", "b"], ["c"]]
