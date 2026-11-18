"""AOI derivation on a MADE-UP water-frequency raster (no satellite data): a reservoir blob at an anchor, a small pond,
and a straight 40 m channel to a weir."""
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("rasterio")
pytest.importorskip("skimage")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sat"))
import derive_aois as d  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402


def utm_raster(res, h, w, lat0, lng0):
    from pyproj import Transformer

    x, y = Transformer.from_crs("EPSG:4326", d.UTM, always_xy=True).transform(lng0, lat0)
    tf = from_origin(x - w * res / 2, y + h * res / 2, res, res)
    return d.Raster(freq=np.zeros((h, w), "float32"), count=np.full((h, w), 50, "float32"), transform=tf, res_m=res)


def test_reservoir_picks_the_big_patch_at_the_anchor():
    r = utm_raster(20, 200, 200, 8.45, 77.31)
    r.freq[80:120, 80:130] = 0.6   # reservoir, 40x50 px at the centre
    r.freq[10:14, 10:14] = 0.9     # far pond
    m, info = d.reservoir(r, (8.45, 77.31), min_freq=0.05, min_count=10, search_m=500)
    assert m is not None and info["pixels"] == 40 * 50
    assert info["area_km2"] == pytest.approx(40 * 50 * 400 / 1e6)


def test_reservoir_reports_when_nothing_is_wet():
    r = utm_raster(20, 100, 100, 8.45, 77.31)
    m, why = d.reservoir(r, (8.45, 77.31), min_freq=0.05, min_count=10, search_m=500)
    assert m is None and "no often-wet patch" in why


def test_too_few_passes_is_not_water():
    r = utm_raster(20, 100, 100, 8.45, 77.31)
    r.freq[40:60, 40:60] = 0.9
    r.count[:] = 3
    m, _ = d.reservoir(r, (8.45, 77.31), min_freq=0.05, min_count=10, search_m=500)
    assert m is None


def test_river_reach_finds_the_straight_wide_channel():
    r = utm_raster(10, 300, 300, 8.40, 77.26)
    r.freq[50:250, 148:152] = 0.9  # 40 m wide, 2 km long, north-south, through the centre
    row, col = 150, 150
    lat_lng = _rc_to_latlng(r, row + 90, col)  # "weir" near the south end of the channel
    poly, info = d.river_reach(r, np.zeros_like(r.freq, bool), (0, 0), lat_lng, min_freq=0.5, min_count=10,
                               min_width_m=30, reach_m=800, min_straightness=0.8)
    assert poly is not None, info
    assert 700 <= info["centreline_m"] <= 820
    assert info["width_m_median"] >= 30


def test_river_reach_refuses_a_narrow_channel():
    r = utm_raster(10, 300, 300, 8.40, 77.26)
    r.freq[50:250, 149:151] = 0.9  # 20 m wide
    poly, why = d.river_reach(r, np.zeros_like(r.freq, bool), (0, 0), _rc_to_latlng(r, 200, 150), min_freq=0.5,
                              min_count=10, min_width_m=30, reach_m=800, min_straightness=0.8)
    assert poly is None and "at least 30 m" in why


def test_falls_box_contains_both_points():
    from pyproj import Transformer
    from shapely.geometry import Point

    w, f = d.ANCHORS["thirparappu_weir"][:2], d.ANCHORS["thirparappu_falls"][:2]
    box, info = d.falls_box(w, f)
    t = Transformer.from_crs("EPSG:4326", d.UTM, always_xy=True)
    assert box.contains(Point(*t.transform(w[1], w[0]))) and box.contains(Point(*t.transform(f[1], f[0])))
    assert 150 <= info["weir_to_falls_m"] <= 300


def _rc_to_latlng(r, row, col):
    from pyproj import Transformer

    x, y = r.transform * (col + 0.5, row + 0.5)
    lng, lat = Transformer.from_crs(d.UTM, "EPSG:4326", always_xy=True).transform(x, y)
    return lat, lng
