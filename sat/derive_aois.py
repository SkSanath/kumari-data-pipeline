"""Derive the areas of interest from a Sentinel-1 water-frequency map (SATELLITE_WATER_SPEC.md §5, "Phase 2" method).

No coordinates are drawn by hand. The only fixed positions are sourced anchor points (Wikidata / OpenStreetMap, already
in the site's place data); everything else comes from the radar data:

1. CDSE Process API, Sentinel-1 GRD IW, descending passes, terrain-flattened gamma0 VV: for every pixel, the share of
   passes in the period where VV < threshold dB (= looks like open water). One small GeoTIFF per area, no full scenes.
2. Reservoirs: pixels that were water in >= 5% of passes, the connected patch at each dam's anchor, + 100 m buffer.
3. River reach: the Kodayar between Pechiparai and the Thirparappu weir; the longest straight run of channel at least
   `min_width_m` wide (often-water pixels), with its centreline length.
4. Falls box: a DRAFT from the OSM weir and the falls point, extended 200 m downstream; replaced by the owner's GPS pins.

All outputs are "derived-draft" for the owner to check on the preview PNGs. Credit: "Contains modified Copernicus
Sentinel data [years]".

  python sat/derive_aois.py --out out/aoi          # needs CDSE_CLIENT_ID / CDSE_CLIENT_SECRET
  python sat/derive_aois.py --out out/aoi --from-tif out/aoi   # re-vectorise saved rasters, no requests
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

UTM = "EPSG:32643"  # WGS 84 / UTM 43N (Kanniyakumari)
TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"

# Anchors: sourced points from the site's place data (data/seed/places.generated.json).
ANCHORS = {
    "pechiparai_reservoir": (8.451, 77.31571, "Wikidata Q13258211"),
    "pechiparai_dam": (8.4513653, 77.3078214, "OSM node/7016828785"),
    "chittar1_dam": (8.4353514, 77.285927, "OSM way/551822483 (Wikidata Q124767577)"),
    "chittar2_dam": (8.4335791, 77.2525442, "OSM way/551822484 (Wikidata Q124767649)"),
    "thirparappu_weir": (8.392765, 77.2580466, "OSM way/491055532"),
    "thirparappu_falls": (8.391332, 77.2593978, "OSM node/4832179568 (Wikidata Q37924)"),
}

# Two requests: a 20 m overview for the reservoirs, a 10 m one for the river between Pechiparai and the falls.
AREAS = {
    "reservoirs": {"bbox_lonlat": (77.22, 8.38, 77.40, 8.51), "res_m": 20},
    "river": {"bbox_lonlat": (77.235, 8.380, 77.320, 8.462), "res_m": 10},
}

EVALSCRIPT = """//VERSION=3
function setup() {
  return { input: [{ bands: ["VV", "dataMask"] }],
           output: { bands: 2, sampleType: "FLOAT32" },
           mosaicking: "ORBIT" };
}
function evaluatePixel(samples) {
  var wet = 0, n = 0;
  for (var i = 0; i < samples.length; i++) {
    var s = samples[i];
    if (!s.dataMask || !(s.VV > 0)) continue;
    n++;
    if (10 * Math.log10(s.VV) < THRESHOLD_DB) wet++;
  }
  return [n > 0 ? wet / n : -1, n];
}"""


@dataclass
class Raster:
    freq: np.ndarray  # share of passes classed water; -1 = no data
    count: np.ndarray
    transform: object  # rasterio Affine
    res_m: float


# ---------------------------------------------------------------- fetching

def to_utm_bbox(bbox_lonlat):
    from pyproj import Transformer

    t = Transformer.from_crs("EPSG:4326", UTM, always_xy=True)
    xs, ys = zip(*(t.transform(x, y) for x in bbox_lonlat[0::2] for y in bbox_lonlat[1::2]))
    return [math.floor(min(xs)), math.floor(min(ys)), math.ceil(max(xs)), math.ceil(max(ys))]


def token() -> str:
    import requests

    cid = os.environ.get("CDSE_CLIENT_ID", "").strip()
    sec = os.environ.get("CDSE_CLIENT_SECRET", "").strip()
    r = requests.post(TOKEN_URL, data={"grant_type": "client_credentials", "client_id": cid, "client_secret": sec}, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def fetch(area: str, start: str, end: str, threshold_db: float, out: Path, tok: str) -> Path:
    import requests

    a = AREAS[area]
    bbox = to_utm_bbox(a["bbox_lonlat"])
    body = {
        "input": {
            "bounds": {"bbox": bbox, "properties": {"crs": f"http://www.opengis.net/def/crs/EPSG/0/{UTM.split(':')[1]}"}},
            "data": [{
                "type": "sentinel-1-grd",
                "dataFilter": {"timeRange": {"from": f"{start}T00:00:00Z", "to": f"{end}T23:59:59Z"},
                               "acquisitionMode": "IW", "polarization": "DV", "orbitDirection": "DESCENDING", "resolution": "HIGH"},
                "processing": {"backCoeff": "GAMMA0_TERRAIN", "orthorectify": True, "demInstance": "COPERNICUS_30",
                               "speckleFilter": {"type": "LEE", "windowSizeX": 5, "windowSizeY": 5}},
            }],
        },
        "output": {"resx": a["res_m"], "resy": a["res_m"], "responses": [{"identifier": "default", "format": {"type": "image/tiff"}}]},
        "evalscript": EVALSCRIPT.replace("THRESHOLD_DB", str(threshold_db)),
    }
    t0 = time.time()
    for attempt in range(6):
        r = requests.post(PROCESS_URL, json=body, headers={"Authorization": f"Bearer {tok}", "Accept": "image/tiff"}, timeout=600)
        if r.status_code != 429:
            break
        # Free plan: processing units per minute are limited. Wait as told (or a minute) and try again.
        wait = int(float(r.headers.get("Retry-After", 0) or 0)) or 60 * (attempt + 1)
        wait = min(max(wait, 30), 300)
        print(f"{area}: rate limited (429), waiting {wait} s")
        time.sleep(wait)
    if r.status_code != 200:
        sys.exit(f"{area}: Process API HTTP {r.status_code}: {r.text[:500]}")
    path = out / f"{area}_freq.tif"
    path.write_bytes(r.content)
    pu = r.headers.get("x-processingunits-spent", "?")
    print(f"{area}: {len(r.content)} bytes, {pu} processing units, {time.time() - t0:.0f} s, bbox UTM {bbox}, {a['res_m']} m")
    return path


def load(path: Path) -> Raster:
    import rasterio

    with rasterio.open(path) as src:
        a = src.read().astype("float32")
        return Raster(freq=a[0], count=a[1], transform=src.transform, res_m=abs(src.transform.a))


# ---------------------------------------------------------------- geometry helpers

def lonlat_to_rc(r: Raster, lat: float, lng: float) -> tuple[int, int]:
    from pyproj import Transformer

    x, y = Transformer.from_crs("EPSG:4326", UTM, always_xy=True).transform(lng, lat)
    col, row = ~r.transform * (x, y)
    return int(row), int(col)


def mask_to_utm_polygon(mask: np.ndarray, transform):
    from rasterio import features
    from shapely.geometry import shape
    from shapely.ops import unary_union

    polys = [shape(g) for g, v in features.shapes(mask.astype("uint8"), mask=mask, transform=transform) if v == 1]
    return unary_union(polys)


def utm_to_wgs84(geom):
    from pyproj import Transformer
    from shapely.ops import transform as tr

    t = Transformer.from_crs(UTM, "EPSG:4326", always_xy=True)
    return tr(lambda x, y, z=None: t.transform(x, y), geom)


def round_coords(geojson_geom: dict, nd: int = 6) -> dict:
    def rnd(c):
        return [round(v, nd) for v in c] if isinstance(c[0], (int, float)) else [rnd(x) for x in c]

    return {**geojson_geom, "coordinates": rnd(geojson_geom["coordinates"])}


# ---------------------------------------------------------------- reservoirs

def reservoir(r: Raster, anchor: tuple[float, float], min_freq: float, min_count: int, search_m: float, open_px: int = 1):
    """Connected often-wet patch nearest the anchor (within search_m). Returns (mask, info) or (None, reason)."""
    from skimage import measure, morphology

    valid = r.count >= min_count
    wet0 = (r.freq >= min_freq) & valid
    # Cut thin links (river channels, paddy strips) so separate reservoirs stay separate.
    wet = morphology.binary_opening(wet0, morphology.disk(open_px)) if open_px else wet0
    lab = measure.label(wet, connectivity=2)
    row, col = lonlat_to_rc(r, *anchor)
    rad = int(search_m / r.res_m)
    r0, r1 = max(0, row - rad), min(lab.shape[0], row + rad + 1)
    c0, c1 = max(0, col - rad), min(lab.shape[1], col + rad + 1)
    window = lab[r0:r1, c0:c1]
    ids, counts = np.unique(window[window > 0], return_counts=True)
    if not len(ids):
        return None, f"no often-wet patch within {search_m:.0f} m of the anchor"
    # The biggest patch touching the search window: a reservoir dwarfs nearby ponds.
    sizes = {i: int((lab == i).sum()) for i in ids}
    best = max(sizes, key=sizes.get)
    m = lab == best
    if open_px:
        # Give back the edge pixels the opening shaved off (only original wet pixels next to the patch).
        m = morphology.binary_dilation(m, morphology.disk(open_px)) & wet0
    return m, {"pixels": int(m.sum()), "area_km2": round(float(m.sum()) * r.res_m ** 2 / 1e6, 3)}


# ---------------------------------------------------------------- river reach

def river_reach(r: Raster, exclude, start_anchor, end_anchor, min_freq: float, min_count: int,
                min_width_m: float, reach_m: float, min_straightness: float):
    """Longest straight run of channel >= min_width_m between two anchors. Returns (polygon_utm, info) or (None, reason)."""
    from skimage import measure, morphology
    from scipy import ndimage as ndi

    wet = (r.freq >= min_freq) & (r.count >= min_count) & ~exclude
    wet = morphology.remove_small_objects(wet, 20)
    lab = measure.label(wet, connectivity=2)
    # The channel component nearest the end anchor (the weir pool), searching up to 300 m.
    row, col = lonlat_to_rc(r, *end_anchor)
    rad = int(300 / r.res_m)
    win = lab[max(0, row - rad): row + rad + 1, max(0, col - rad): col + rad + 1]
    ids = np.unique(win[win > 0])
    if not len(ids):
        return None, "no often-wet channel within 300 m of the weir"
    chan = np.isin(lab, ids)
    dist = ndi.distance_transform_edt(chan) * r.res_m  # metres to the bank
    skel = morphology.skeletonize(chan)
    width = 2 * dist * skel
    wide = skel & (width >= min_width_m)
    runs = measure.label(wide, connectivity=2)
    best = None
    for reg in measure.regionprops(runs):
        rc = reg.coords
        if len(rc) < 5:
            continue
        # Straightness: distance between the two farthest pixels vs the number of steps along the run.
        d = np.hypot(*(rc[:, None, :] - rc[None, :, :]).transpose(2, 0, 1)) if len(rc) <= 2000 else None
        if d is None:
            continue
        i, j = np.unravel_index(np.argmax(d), d.shape)
        chord = d[i, j] * r.res_m
        path = len(rc) * r.res_m
        straight = chord / path if path else 0
        if straight < min_straightness:
            continue
        if best is None or chord > best[0]:
            best = (chord, reg, rc[i], rc[j], straight)
    if best is None:
        return None, f"no straight run of channel at least {min_width_m:.0f} m wide"
    chord, reg, a, b, straight = best
    # Trim to at most reach_m around the middle of the run.
    rc = reg.coords
    mid = (a + b) / 2
    keep = np.hypot(*(rc - mid).T) * r.res_m <= reach_m / 2
    rc = rc[keep]
    mask = np.zeros_like(chan)
    mask[rc[:, 0], rc[:, 1]] = True
    wmax = float(width[mask].max())
    grow = int(math.ceil(wmax / 2 / r.res_m)) + 1
    reach = morphology.binary_dilation(mask, morphology.disk(grow)) & chan
    from shapely.geometry import LineString

    ends = rc[np.argmin(rc @ (b - a))], rc[np.argmax(rc @ (b - a))]
    xy = [r.transform * (c + 0.5, rr + 0.5) for rr, c in ends]
    line = LineString(xy)
    widths = width[mask]
    return mask_to_utm_polygon(reach, r.transform), {
        "centreline_m": round(line.length), "centreline_utm": [list(map(round, p)) for p in xy],
        "width_m_median": round(float(np.median(widths))), "width_m_min": round(float(widths.min())),
        "pixels_across_min": round(float(widths.min()) / r.res_m, 1), "straightness": round(straight, 2),
        "pixels": int(reach.sum()),
    }


# ---------------------------------------------------------------- falls box (draft)

def falls_box(weir, falls, downstream_m=200.0, half_width_m=60.0):
    from pyproj import Transformer
    from shapely.geometry import LineString

    t = Transformer.from_crs("EPSG:4326", UTM, always_xy=True)
    wx, wy = t.transform(weir[1], weir[0])
    fx, fy = t.transform(falls[1], falls[0])
    dx, dy = fx - wx, fy - wy
    n = math.hypot(dx, dy)
    ex, ey = fx + dx / n * downstream_m, fy + dy / n * downstream_m
    line = LineString([(wx - dx / n * 30, wy - dy / n * 30), (ex, ey)])
    return line.buffer(half_width_m, cap_style=2), {"weir_to_falls_m": round(n), "downstream_m": downstream_m, "half_width_m": half_width_m}


# ---------------------------------------------------------------- previews

def preview(r: Raster, outlines, path: Path, points=()):
    """Grey = share of passes classed water (white = always). Coloured lines = outlines; red dots = anchors."""
    import rasterio
    from rasterio import features

    f = np.clip(np.where(r.freq < 0, 0, r.freq), 0, 1)
    g = (np.sqrt(f) * 255).astype("uint8")
    rgb = np.stack([g, g, g])
    colours = [(255, 200, 0), (0, 220, 255), (255, 0, 255), (0, 255, 0), (255, 120, 0)]
    for k, geom in enumerate(outlines):
        if geom is None or geom.is_empty:
            continue
        edge = features.rasterize([(geom.boundary.buffer(r.res_m * 0.8), 1)], out_shape=f.shape, transform=r.transform).astype(bool)
        for ch in range(3):
            rgb[ch][edge] = colours[k % len(colours)][ch]
    for lat, lng in points:
        row, col = lonlat_to_rc(r, lat, lng)
        rr, cc = np.ogrid[: f.shape[0], : f.shape[1]]
        dot = (rr - row) ** 2 + (cc - col) ** 2 <= 9
        rgb[0][dot], rgb[1][dot], rgb[2][dot] = 255, 0, 0
    with rasterio.open(path, "w", driver="PNG", width=f.shape[1], height=f.shape[0], count=3, dtype="uint8") as dst:
        dst.write(rgb)


# ---------------------------------------------------------------- main

def feature(geom_utm, props) -> dict:
    from shapely.geometry import mapping

    g = utm_to_wgs84(geom_utm.simplify(5))
    return {"type": "FeatureCollection", "status": "derived-draft", "crs_note": "WGS84 (EPSG:4326), lon/lat order",
            "features": [{"type": "Feature", "properties": props, "geometry": round_coords(mapping(g))}]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out/aoi")
    ap.add_argument("--from", dest="start", default="2024-01-01")
    ap.add_argument("--to", dest="end", default="2025-12-31")
    ap.add_argument("--threshold-db", type=float, default=-18.0)
    ap.add_argument("--from-tif", help="folder with saved *_freq.tif: skip the requests")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    if a.from_tif:
        paths = {k: Path(a.from_tif) / f"{k}_freq.tif" for k in AREAS}
    else:
        tok = token()
        paths = {k: fetch(k, a.start, a.end, a.threshold_db, out, tok) for k in AREAS}
    res, riv = load(paths["reservoirs"]), load(paths["river"])
    for name, rr in (("reservoirs", res), ("river", riv)):
        ok = rr.count[rr.count > 0]
        print(f"{name}: passes per pixel min {int(ok.min()) if ok.size else 0}, median {int(np.median(ok)) if ok.size else 0}")

    years = f"{a.start[:4]}-{a.end[:4]}" if a.start[:4] != a.end[:4] else a.start[:4]
    method = {"data": "Sentinel-1 GRD IW descending, gamma0 terrain-flattened VV, Lee 5x5", "period": f"{a.start}..{a.end}",
              "water_if_vv_below_db": a.threshold_db, "credit": f"Contains modified Copernicus Sentinel data {years}",
              "calibrated": False}
    report, outlines_res, outlines_riv = {}, [], []
    min_count = 10
    for name, anchor_key, buffer_m in (("pechiparai_max", "pechiparai_reservoir", 100), ("chittar1_max", "chittar1_dam", 100), ("chittar2_max", "chittar2_dam", 100)):
        lat, lng, src = ANCHORS[anchor_key]
        m, info = reservoir(res, (lat, lng), min_freq=0.05, min_count=min_count, search_m=500)
        if m is None:
            print(f"{name}: NOT derived: {info}")
            report[name] = {"derived": False, "why": info}
            continue
        poly = mask_to_utm_polygon(m, res.transform)
        poly_b = poly.buffer(buffer_m)
        outlines_res.append(poly_b)
        props = {"what": name, "anchor": {"lat": lat, "lng": lng, "source": src}, "water_freq_min": 0.05, "buffer_m": buffer_m,
                 "max_water_area_km2": info["area_km2"], "pixels_at_20m": info["pixels"], "method": method,
                 "todo": "Owner: check the outline on the preview; Phase 2 water-frequency method (spec §5)."}
        (out / f"{name}.geojson").write_text(json.dumps(feature(poly_b, props), indent=1))
        report[name] = {"derived": True, **info}
        print(f"{name}: {info['area_km2']} km2 max water, {info['pixels']} px at {res.res_m:.0f} m")

    # River: exclude the reservoirs, then the straight reach upstream of the weir.
    from rasterio import features as rf

    excl = np.zeros(riv.freq.shape, bool)
    if outlines_res:
        excl = rf.rasterize([(g, 1) for g in outlines_res], out_shape=riv.freq.shape, transform=riv.transform).astype(bool)
    for min_w in (30.0, 20.0):
        poly, info = river_reach(riv, excl, ANCHORS["pechiparai_dam"][:2], ANCHORS["thirparappu_weir"][:2],
                                 min_freq=0.5, min_count=min_count, min_width_m=min_w, reach_m=800, min_straightness=0.8)
        if poly is not None:
            break
        print(f"kodayar_reach (min width {min_w:.0f} m): {info}")
    if poly is not None:
        outlines_riv.append(poly)
        props = {"what": "kodayar_reach", "water_freq_min": 0.5, "min_width_m": min_w, **info, "method": method,
                 "todo": "Owner: confirm this is the Kodayar between Pechiparai and the falls (not a canal) on the preview."}
        (out / "kodayar_reach.geojson").write_text(json.dumps(feature(poly, props), indent=1))
        report["kodayar_reach"] = {"derived": True, **{k: v for k, v in info.items() if k != "centreline_utm"}}
        print(f"kodayar_reach: {info['centreline_m']} m long, width median {info['width_m_median']} m (min {info['width_m_min']} m)")
    else:
        report["kodayar_reach"] = {"derived": False, "why": info}

    w, f = ANCHORS["thirparappu_weir"], ANCHORS["thirparappu_falls"]
    box, info = falls_box(w[:2], f[:2])
    outlines_riv.append(box)
    props = {"what": "falls_box", "status_note": "DRAFT from OSM weir + falls point; replace with owner's GPS pins",
             "weir": {"lat": w[0], "lng": w[1], "source": w[2]}, "falls": {"lat": f[0], "lng": f[1], "source": f[2]}, **info}
    (out / "falls_box.geojson").write_text(json.dumps(feature(box, props), indent=1))
    report["falls_box"] = {"derived": "draft", **info}

    pts = [ANCHORS[k][:2] for k in ANCHORS]
    preview(res, outlines_res, out / "preview_reservoirs.png", pts)
    preview(riv, outlines_res + outlines_riv, out / "preview_river.png", pts)
    (out / "report.json").write_text(json.dumps(report, indent=1))
    print("RESULT:", json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
