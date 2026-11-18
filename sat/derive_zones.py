"""Derive the rain catchment zones from the Copernicus DEM GLO-30 (SATELLITE_WATER_SPEC.md §5, build step 7).

  Zone A = land draining into Pechiparai reservoir (the owner-checked outline `aoi/pechiparai_max.geojson`).
  Zone B = land draining to the Kodayar between the dam and Thirparappu Falls: everything upstream of the falls,
           minus zone A (this includes the Chittar I/II catchments, whose streams join below Pechiparai).

Method (no coordinates drawn by hand):
1. One CDSE Process API request: the DEM (COPERNICUS_30) over a bbox around the basin, resampled to 30 m in UTM 43N.
2. Flow routing by priority-flood (Barnes et al. 2014): cells are visited from the bbox edge inwards in order of
   (filled) elevation; each cell drains to the neighbour it was reached from. Pits and flats drain sensibly, and every
   cell has exactly one path to the edge, so a catchment is just "every cell whose path reaches the target".
3. The falls point (OSM/Wikidata) is snapped to the cell with the largest upstream area within `snap_m`.
4. Checks: a zone touching the bbox edge means the bbox is too small (the run fails rather than cut a zone short).

Outputs `catchment_zone_a.geojson`, `catchment_zone_b.geojson` (status derived-draft) and a preview PNG for the owner.

  python sat/derive_zones.py --out out/zones              # needs CDSE_CLIENT_ID / CDSE_CLIENT_SECRET
  python sat/derive_zones.py --out out/zones --from-tif out/zones/dem.tif   # no request
"""
from __future__ import annotations

import argparse
import heapq
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sat"))

UTM = "EPSG:32643"
# Generous box around the Kodayar basin above Thirparappu (the Western Ghats crest lies to the north-east).
BBOX_LONLAT = (77.08, 8.30, 77.52, 8.78)
RES_M = 30
FALLS = (8.391332, 77.2593978, "OSM node/4832179568 (Wikidata Q37924)")
POINTS = {
    "pechiparai_dam": (8.4513653, 77.3078214),
    "chittar1_dam": (8.4353514, 77.285927),
    "chittar2_dam": (8.4335791, 77.2525442),
    "perunchani_dam": None,  # filled from the site data when present; only drawn on the preview
}

EVALSCRIPT = """//VERSION=3
function setup() { return { input: ["DEM"], output: { bands: 1, sampleType: "FLOAT32" } }; }
function evaluatePixel(s) { return [s.DEM]; }"""

# D8 neighbour offsets
NB = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


# ---------------------------------------------------------------- routing (pure, tested)

def route(dem: np.ndarray, nodata: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Priority-flood flow routing. Returns (parent, order): parent[i] = flat index the cell drains to (-1 = drains
    off the edge or nodata), order = flat indices in visiting order (every parent is visited before its children)."""
    h, w = dem.shape
    n = h * w
    z = dem.ravel().astype("float64")
    bad = np.zeros(n, bool) if nodata is None else nodata.ravel().copy()
    parent = np.full(n, -1, dtype=np.int64)
    seen = bad.copy()
    heap: list[tuple[float, int, int]] = []
    tick = 0
    for r in range(h):
        for c in range(w):
            if r in (0, h - 1) or c in (0, w - 1):
                i = r * w + c
                if not seen[i]:
                    seen[i] = True
                    heapq.heappush(heap, (z[i], tick, i)); tick += 1
    # Cells next to nodata also drain out (sea, gaps).
    if bad.any():
        for i in np.flatnonzero(bad):
            r, c = divmod(int(i), w)
            for dr, dc in NB:
                rr, cc = r + dr, c + dc
                if 0 <= rr < h and 0 <= cc < w:
                    j = rr * w + cc
                    if not seen[j]:
                        seen[j] = True
                        heapq.heappush(heap, (z[j], tick, j)); tick += 1
    order = np.empty(n - int(bad.sum()), dtype=np.int64)
    k = 0
    while heap:
        zi, _, i = heapq.heappop(heap)
        order[k] = i; k += 1
        r, c = divmod(i, w)
        for dr, dc in NB:
            rr, cc = r + dr, c + dc
            if 0 <= rr < h and 0 <= cc < w:
                j = rr * w + cc
                if not seen[j]:
                    seen[j] = True
                    parent[j] = i
                    heapq.heappush(heap, (max(z[j], zi), tick, j)); tick += 1
    return parent, order[:k]


def accumulation(parent: np.ndarray, order: np.ndarray) -> np.ndarray:
    """Upstream cell count (including the cell itself)."""
    acc = np.zeros(parent.shape[0], dtype=np.int64)
    acc[order] = 1
    for i in order[::-1]:
        p = parent[i]
        if p >= 0:
            acc[p] += acc[i]
    return acc


def upstream(parent: np.ndarray, order: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Cells whose drainage path reaches any target cell (targets included). One pass, downstream first."""
    t = target.ravel()
    m = t.copy()
    for i in order:
        p = parent[i]
        if not m[i] and p >= 0 and m[p]:
            m[i] = True
    return m


def snap(acc: np.ndarray, shape: tuple[int, int], rc: tuple[int, int], radius_px: int) -> tuple[int, int]:
    """The cell with the largest upstream area within radius of rc."""
    h, w = shape
    r0, c0 = rc
    a = acc.reshape(shape)
    best, best_rc = -1, rc
    for r in range(max(0, r0 - radius_px), min(h, r0 + radius_px + 1)):
        for c in range(max(0, c0 - radius_px), min(w, c0 + radius_px + 1)):
            if (r - r0) ** 2 + (c - c0) ** 2 <= radius_px ** 2 and a[r, c] > best:
                best, best_rc = int(a[r, c]), (r, c)
    return best_rc


def touches_edge(mask: np.ndarray) -> bool:
    return bool(mask[0, :].any() or mask[-1, :].any() or mask[:, 0].any() or mask[:, -1].any())


# ---------------------------------------------------------------- data

def fetch_dem(out: Path) -> Path:
    from cdse import Cdse
    from derive_aois import to_utm_bbox

    bbox = to_utm_bbox(BBOX_LONLAT)
    body = {
        "input": {"bounds": {"bbox": bbox, "properties": {"crs": f"http://www.opengis.net/def/crs/EPSG/0/{UTM.split(':')[1]}"}},
                  "data": [{"type": "dem", "dataFilter": {"demInstance": "COPERNICUS_30"}, "processing": {"upsampling": "BILINEAR", "downsampling": "BILINEAR"}}]},
        "output": {"resx": RES_M, "resy": RES_M, "responses": [{"identifier": "default", "format": {"type": "image/tiff"}}]},
        "evalscript": EVALSCRIPT,
    }
    c = Cdse()
    path = out / "dem.tif"
    path.write_bytes(c.process(body, "dem"))
    print(f"DEM bbox UTM {bbox}, {RES_M} m, {c.pu:.1f} PU")
    return path


def rasterize(geojson_path: Path, shape, transform) -> np.ndarray:
    from pyproj import Transformer
    from rasterio import features
    from shapely.geometry import shape as shp
    from shapely.ops import transform as tr

    j = json.loads(geojson_path.read_text())
    t = Transformer.from_crs("EPSG:4326", UTM, always_xy=True)
    g = tr(lambda x, y, z=None: t.transform(x, y), shp(j["features"][0]["geometry"]))
    return features.rasterize([(g, 1)], out_shape=shape, transform=transform).astype(bool)


def rc_of(lat: float, lng: float, transform) -> tuple[int, int]:
    from pyproj import Transformer

    x, y = Transformer.from_crs("EPSG:4326", UTM, always_xy=True).transform(lng, lat)
    col, row = ~transform * (x, y)
    return int(row), int(col)


def preview(dem: np.ndarray, transform, outlines, points, path: Path) -> None:
    import rasterio
    from rasterio import features

    d = np.where(np.isfinite(dem), dem, np.nanmin(dem))
    gy, gx = np.gradient(d, RES_M)
    shade = np.clip(0.5 + 0.5 * (-gx - gy) / (np.hypot(gx, gy) + 1e-6) * np.clip(np.hypot(gx, gy) * 3, 0, 1), 0, 1)
    lo, hi = np.percentile(d, [2, 98])
    g = (np.clip((d - lo) / (hi - lo + 1e-9), 0, 1) * 0.5 + shade * 0.5) * 255
    rgb = np.stack([g, g, g]).astype("uint8")
    colours = [(255, 200, 0), (0, 220, 255), (255, 0, 255)]
    for k, geom in enumerate(outlines):
        if geom is None or geom.is_empty:
            continue
        edge = features.rasterize([(geom.boundary.buffer(RES_M * 1.5), 1)], out_shape=d.shape, transform=transform).astype(bool)
        for ch in range(3):
            rgb[ch][edge] = colours[k % len(colours)][ch]
    for r, c in points:
        rr, cc = np.ogrid[: d.shape[0], : d.shape[1]]
        dot = (rr - r) ** 2 + (cc - c) ** 2 <= 16
        rgb[0][dot], rgb[1][dot], rgb[2][dot] = 255, 0, 0
    with rasterio.open(path, "w", driver="PNG", width=d.shape[1], height=d.shape[0], count=3, dtype="uint8") as dst:
        dst.write(rgb)


def main() -> int:
    import rasterio
    from derive_aois import feature, mask_to_utm_polygon

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out/zones")
    ap.add_argument("--from-tif")
    ap.add_argument("--snap-m", type=float, default=300)
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    tif = Path(a.from_tif) if a.from_tif else fetch_dem(out)
    with rasterio.open(tif) as src:
        dem = src.read(1).astype("float64"); transform = src.transform
    nodata = ~np.isfinite(dem) | (dem < -100)
    print(f"DEM {dem.shape[1]}x{dem.shape[0]} px, elevation {np.nanmin(np.where(nodata, np.nan, dem)):.0f}..{np.nanmax(np.where(nodata, np.nan, dem)):.0f} m, nodata {int(nodata.sum())} px")

    parent, order = route(np.where(nodata, -1e9, dem), nodata)
    acc = accumulation(parent, order)
    cell_km2 = RES_M * RES_M / 1e6

    lake = rasterize(ROOT / "aoi" / "pechiparai_max.geojson", dem.shape, transform)
    zone_a = upstream(parent, order, lake).reshape(dem.shape)
    fr, fc = snap(acc, dem.shape, rc_of(FALLS[0], FALLS[1], transform), int(a.snap_m / RES_M))
    falls_t = np.zeros(dem.shape, bool); falls_t[fr, fc] = True
    above_falls = upstream(parent, order, falls_t).reshape(dem.shape)
    zone_b = above_falls & ~zone_a

    rep = {
        "zone_a_km2": round(zone_a.sum() * cell_km2, 1), "zone_b_km2": round(zone_b.sum() * cell_km2, 1),
        "above_falls_km2": round(above_falls.sum() * cell_km2, 1),
        "zone_a_inside_falls_basin": bool((zone_a & above_falls).sum() == zone_a.sum()),
        "falls_snapped_m": round(float(np.hypot(fr - rc_of(FALLS[0], FALLS[1], transform)[0], fc - rc_of(FALLS[0], FALLS[1], transform)[1])) * RES_M),
    }
    for name, p in POINTS.items():
        if p:
            r, c = snap(acc, dem.shape, rc_of(p[0], p[1], transform), int(150 / RES_M))
            rep[f"{name}_in"] = "A" if zone_a[r, c] else "B" if zone_b[r, c] else "outside"
    print(json.dumps(rep, indent=1))
    for nm, m in (("zone A", zone_a), ("falls basin", above_falls)):
        if touches_edge(m):
            sys.exit(f"{nm} touches the edge of the DEM box: enlarge BBOX_LONLAT and run again")
    if not rep["zone_a_inside_falls_basin"]:
        print("WARNING: part of zone A does not drain to the falls (check the preview)")

    poly_a, poly_b = mask_to_utm_polygon(zone_a, transform), mask_to_utm_polygon(zone_b, transform)
    common = {"source": "Copernicus DEM GLO-30 via CDSE Process API, priority-flood D8 routing", "res_m": RES_M,
              "falls_anchor": FALLS[2], "status_note": "derived-draft; owner checks the preview"}
    (out / "catchment_zone_a.geojson").write_text(json.dumps(feature(poly_a.buffer(0), {**common, "what": "catchment_zone_a", "desc": "land draining into Pechiparai reservoir", "area_km2": rep["zone_a_km2"]}), indent=1))
    (out / "catchment_zone_b.geojson").write_text(json.dumps(feature(poly_b.buffer(0), {**common, "what": "catchment_zone_b", "desc": "land draining to the Kodayar between Pechiparai and Thirparappu Falls", "area_km2": rep["zone_b_km2"]}), indent=1))
    (out / "zones_report.json").write_text(json.dumps(rep, indent=1))
    pts = [(fr, fc)] + [snap(acc, dem.shape, rc_of(p[0], p[1], transform), 5) for p in POINTS.values() if p]
    lake_poly = mask_to_utm_polygon(lake, transform)
    preview(dem, transform, [poly_a, poly_b, lake_poly], pts, out / "preview_zones.png")
    print("wrote zones and preview")
    return 0


if __name__ == "__main__":
    sys.exit(main())
