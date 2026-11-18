"""Flow routing on small made-up DEMs (no network)."""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sat"))
from derive_zones import accumulation, route, snap, touches_edge, upstream  # noqa: E402


def valley(h=21, w=21):
    """A V-shaped valley draining south along the middle column."""
    r, c = np.mgrid[0:h, 0:w]
    return (np.abs(c - w // 2) * 10 + (h - r) * 1.0).astype(float)


def test_every_cell_drains_and_parents_come_first():
    dem = valley()
    parent, order = route(dem)
    assert len(order) == dem.size
    pos = np.empty(dem.size, int); pos[order] = np.arange(dem.size)
    for i in range(dem.size):
        if parent[i] >= 0:
            assert pos[parent[i]] < pos[i]


def test_valley_bottom_collects_everything_upstream():
    dem = valley()
    parent, order = route(dem)
    acc = accumulation(parent, order).reshape(dem.shape)
    # the outlet at the bottom middle gathers far more than any hillside cell
    assert acc[-2, 10] > acc[-2, 3] * 10


def test_upstream_of_a_point_is_the_valley_above_it():
    dem = valley()
    parent, order = route(dem)
    t = np.zeros(dem.shape, bool); t[15, 10] = True
    up = upstream(parent, order, t).reshape(dem.shape)
    assert up[15, 10] and up[5, 10] and up[5, 4]   # above it, valley floor and slope
    assert not up[18, 10]                          # below it


def test_a_pit_does_not_trap_its_catchment():
    dem = valley()
    dem[8, 10] -= 50  # a hole on the valley floor
    parent, order = route(dem)
    t = np.zeros(dem.shape, bool); t[15, 10] = True
    up = upstream(parent, order, t).reshape(dem.shape)
    assert up[8, 10] and up[3, 10]  # water fills the pit and carries on downhill


def test_snap_moves_to_the_channel():
    dem = valley()
    parent, order = route(dem)
    acc = accumulation(parent, order)
    r, c = snap(acc, dem.shape, (15, 7), 4)
    assert c == 10 and abs(r - 15) <= 4  # onto the valley floor, the largest flow within reach


def test_edge_check():
    m = np.zeros((5, 5), bool); m[2, 2] = True
    assert not touches_edge(m)
    m[0, 2] = True
    assert touches_edge(m)
