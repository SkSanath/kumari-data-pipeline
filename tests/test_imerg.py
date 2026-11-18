"""IMERG helpers with made-up numbers (no network)."""
import sys
from pathlib import Path

import pytest
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))
sys.path.insert(0, str(ROOT / "rain"))
from imerg import cell_index, opendap_url, parse_ascii, weights, zone_mean  # noqa: E402


def test_cell_index_matches_the_imerg_grid():
    # cell centres at -179.95 + 0.1 i: lon 77.25 sits in cell i=2572 (77.2..77.3)
    assert cell_index(77.25, 8.39) == (2572, 983)


def test_weights_split_by_area_and_sum_to_one():
    zone = box(77.25, 8.35, 77.35, 8.45)  # straddles four cells equally
    w = weights(zone)
    assert len(w) == 4
    assert sum(w.values()) == pytest.approx(1)
    assert all(v == pytest.approx(0.25, abs=0.01) for v in w.values())


def test_zone_mean_and_missing_cells():
    w = {(1, 1): 0.75, (1, 2): 0.25}
    assert zone_mean(w, {(1, 1): 10.0, (1, 2): 2.0}) == 8.0
    assert zone_mean(w, {(1, 1): 10.0, (1, 2): -9999.9}) is None  # fill value -> missing, never a guess
    assert zone_mean(w, {(1, 1): 10.0}) is None


def test_parse_dap2_ascii():
    txt = """Dataset {
    Float32 precipitation[time = 1][lon = 2][lat = 3];
} x;
---------------------------------------------
precipitation.precipitation[0][0], 1.5, 0, 2
precipitation.precipitation[0][1], 3, -9999.9, 4.25
"""
    assert parse_ascii(txt) == [[1.5, 0.0, 2.0], [3.0, -9999.9, 4.25]]


def test_parse_dap4_csv():
    txt = """Dataset: 3B-DAY-E.MS.MRG.3IMERG.20311117-S000000-E235959.V07C.nc4
/precipitation[0][0], 0, 0.5
/precipitation[0][1], 2.25, 0
"""
    assert parse_ascii(txt) == [[0.0, 0.5], [2.25, 0.0]]


def test_opendap_link_found_or_derived():
    e = {"links": [{"href": "https://gpm1.gesdisc.eosdis.nasa.gov/opendap/GPM_L3/x.nc4.html"}]}
    assert opendap_url(e) == "https://gpm1.gesdisc.eosdis.nasa.gov/opendap/GPM_L3/x.nc4"
    e = {"links": [{"href": "https://gpm1.gesdisc.eosdis.nasa.gov/data/GPM_L3/y.nc4"}]}
    assert opendap_url(e) == "https://gpm1.gesdisc.eosdis.nasa.gov/opendap/GPM_L3/y.nc4"
