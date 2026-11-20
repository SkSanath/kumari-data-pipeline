"""Falls-box signals at Thirparappu (SATELLITE_WATER_SPEC.md §7.3, build step 8). Weakest signal: supporting only.

Box: `aoi/falls_box.geojson` (weir + falls + ~200 m of riverbed below). Until the owner's GPS pins replace it, the box is a
DRAFT from OSM points, and every row says so (`box_status`).

- Sentinel-1 (every descending pass, relative orbit 165): mean VV and VH gamma0 (dB, terrain-flattened, Lee 5x5) over the
  box, and the dark-pixel share (VV below `dark_db`: smooth open water in the pool). Rougher, faster water is usually
  brighter. Signals `falls_s1_vv_db`, `falls_s1_vh_db`, `falls_s1_dark_frac`.
- Sentinel-2 L2A (clear days only): SCL mask; skipped when under `min_valid_fraction` of the box is valid. Water share =
  valid pixels with MNDWI = (B03 - B11)/(B03 + B11) above its threshold (NDWI = (B03 - B08)/(B03 + B08) also kept).
  Signals `falls_s2_water_frac` (MNDWI) and `falls_s2_ndwi_frac`. A small RGB preview of the latest clear image is saved.
Ten-metre pixels: the box holds only a few hundred, so expect noise. Never drives a level on its own.

Results go to data/falls_box.csv (public: derived Copernicus data) and, with --send, to the site.

  python sat/falls_box.py --from 2024-01-01 --send     # backfill (already processed acquisitions are skipped)
  python sat/falls_box.py --send                       # daily: new acquisitions since the last row
  python sat/falls_box.py --rebuild --from 2024-01-01 --send   # after the box changes: redo everything

Credit: "Contains modified Copernicus Sentinel data [year]".
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sat"))
sys.path.insert(0, str(ROOT / "common"))

UTM = "EPSG:32643"
PLACE = "thirparappu_falls"
CSV_PATH = ROOT / "data" / "falls_box.csv"
PREVIEW = ROOT / "aoi" / "previews" / "falls_s2_latest.png"
FIELDS = ["acquired_at", "sensor", "quality", "why", "valid_fraction", "vv_db", "vh_db", "dark_frac", "water_frac",
          "ndwi_frac", "mndwi_mean", "ndwi_mean", "box_px", "rel_orbit", "platform", "product_ids", "box_status", "processed_at"]

S1_EVAL = """//VERSION=3
function setup() { return { input: [{ bands: ["VV", "VH", "dataMask"] }], output: { bands: 3, sampleType: "FLOAT32" } }; }
function evaluatePixel(s) {
  if (!s.dataMask || !(s.VV > 0) || !(s.VH > 0)) return [NaN, NaN, 0];
  return [10 * Math.log10(s.VV), 10 * Math.log10(s.VH), 1];
}"""

S2_EVAL = """//VERSION=3
function setup() { return { input: [{ bands: ["B02", "B03", "B04", "B08", "B11", "SCL", "dataMask"] }],
                            output: { bands: 7, sampleType: "FLOAT32" }, mosaicking: "SIMPLE" }; }
function evaluatePixel(s) { return [s.B02, s.B03, s.B04, s.B08, s.B11, s.SCL, s.dataMask]; }"""


def settings() -> dict:
    c = yaml.safe_load((ROOT / "config" / "processing.yaml").read_text())
    return {**c["s2_falls"], **c["s1_falls"]}


# ------------------------------------------------------------------ measurements (pure, tested)

def s1_metrics(vv: np.ndarray, vh: np.ndarray, valid: np.ndarray, inside: np.ndarray, dark_db: float, min_valid: float) -> dict:
    n = int(inside.sum())
    use = inside & valid & np.isfinite(vv) & np.isfinite(vh)
    frac = float(use.sum()) / n if n else 0.0
    out = {"valid_fraction": round(frac, 3), "box_px": n}
    if frac < min_valid:
        return {**out, "quality": "missing", "why": f"pass covers {frac:.0%} of the box"}
    # mean of backscatter in linear power, reported in dB (averaging dB values biases low)
    mean_db = lambda a: round(float(10 * np.log10(np.mean(10 ** (a[use] / 10)))), 2)  # noqa: E731
    return {**out, "vv_db": mean_db(vv), "vh_db": mean_db(vh), "dark_frac": round(float((vv[use] < dark_db).mean()), 3),
            "quality": "ok", "why": ""}


def s2_metrics(b03, b08, b11, scl, valid, inside, invalid_scl, min_valid: float, mndwi_t: float, ndwi_t: float) -> dict:
    n = int(inside.sum())
    good = inside & (valid > 0) & ~np.isin(scl.astype(int), list(invalid_scl)) & (scl > 0)
    frac = float(good.sum()) / n if n else 0.0
    out = {"valid_fraction": round(frac, 3), "box_px": n}
    if frac < min_valid:
        return {**out, "quality": "missing", "why": f"only {frac:.0%} of the box clear (cloud or shadow)"}
    with np.errstate(divide="ignore", invalid="ignore"):
        mndwi = (b03 - b11) / (b03 + b11)
        ndwi = (b03 - b08) / (b03 + b08)
    m, d = mndwi[good], ndwi[good]
    ok = np.isfinite(m) & np.isfinite(d)
    m, d = m[ok], d[ok]
    if not len(m):
        return {**out, "quality": "missing", "why": "no usable pixels"}
    return {**out, "water_frac": round(float((m > mndwi_t).mean()), 3), "ndwi_frac": round(float((d > ndwi_t).mean()), 3),
            "mndwi_mean": round(float(m.mean()), 3), "ndwi_mean": round(float(d.mean()), 3), "quality": "ok", "why": ""}


def s2_days(features: list[dict]) -> list[dict]:
    """Catalog items -> one entry per UTC day (tiles and orbits of the same day are one acquisition)."""
    days: dict[str, dict] = {}
    for f in sorted(features, key=lambda f: f["properties"]["datetime"]):
        t = datetime.fromisoformat(f["properties"]["datetime"].replace("Z", "+00:00"))
        d = days.setdefault(t.strftime("%Y-%m-%d"), {"first": t, "ids": [], "platform": f["properties"].get("platform"),
                                                     "cloud": []})
        d["ids"].append(f["id"])
        d["cloud"].append(f["properties"].get("eo:cloud_cover"))
    return list(days.values())


# ------------------------------------------------------------------ geometry and io

def box() -> tuple[object, str]:
    from pyproj import Transformer
    from shapely.geometry import shape
    from shapely.ops import transform as tr

    j = json.loads((ROOT / "aoi" / "falls_box.geojson").read_text())
    if not j["features"]:
        sys.exit("aoi/falls_box.geojson has no box")
    t = Transformer.from_crs("EPSG:4326", UTM, always_xy=True)
    return tr(lambda x, y, z=None: t.transform(x, y), shape(j["features"][0]["geometry"])), j.get("status", "draft")


def grid(g, res: float, margin: float = 60.0):
    import math

    from rasterio.transform import from_origin

    x0, y0, x1, y1 = g.bounds
    x0, y0 = math.floor((x0 - margin) / res) * res, math.floor((y0 - margin) / res) * res
    x1, y1 = math.ceil((x1 + margin) / res) * res, math.ceil((y1 + margin) / res) * res
    w, h = int(round((x1 - x0) / res)), int(round((y1 - y0) / res))
    return [x0, y0, x1, y1], from_origin(x0, y1, res, res), (h, w)


def read_csv() -> list[dict]:
    if not CSV_PATH.exists():
        return []
    with CSV_PATH.open(newline="") as f:
        return list(csv.DictReader(f))


def write_csv(rows: list[dict]) -> None:
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: (r["acquired_at"], r["sensor"]))
    with CSV_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in FIELDS})


def to_measurements(r: dict) -> list[dict]:
    from measurement import measurement

    t = datetime.fromisoformat(r["acquired_at"].replace("Z", "+00:00"))
    fetched = datetime.fromisoformat(r["processed_at"].replace("Z", "+00:00"))
    ids = [i for i in str(r["product_ids"]).split(";") if i]
    url = f"https://catalogue.dataspace.copernicus.eu/odata/v1/Products?$filter=Name%20eq%20'{ids[0]}'" if ids else "https://dataspace.copernicus.eu/"
    source = "Copernicus Sentinel-1 GRD" if r["sensor"] == "s1" else "Copernicus Sentinel-2 L2A"
    meta = {k: r[k] for k in ("valid_fraction", "box_px", "rel_orbit", "platform", "box_status", "why", "mndwi_mean", "ndwi_mean") if r.get(k) not in ("", None)}
    meta["credit"] = f"Contains modified Copernicus Sentinel data {t.year}"
    sig = [("falls_s1_vv_db", "vv_db", "dB"), ("falls_s1_vh_db", "vh_db", "dB"), ("falls_s1_dark_frac", "dark_frac", "fraction")] \
        if r["sensor"] == "s1" else [("falls_s2_water_frac", "water_frac", "fraction"), ("falls_s2_ndwi_frac", "ndwi_frac", "fraction")]
    out = []
    for name, col, unit in sig:
        v = r.get(col)
        v = None if v in ("", None) or r["quality"] == "missing" else float(v)
        out.append(measurement(signal=name, place=PLACE, value=v, unit=unit, observed_at=t, fetched_at=fetched, source=source,
                               source_url=url, product_id=";".join(ids)[:200] or None, quality=r["quality"] if v is not None else "missing",
                               visibility="public", meta=meta))
    return out


def save_preview(b02, b03, b04, inside, path: Path, scale: int = 8) -> None:
    """True colour (reflectance x 3.5, clipped), box edge in yellow, enlarged x scale so the few pixels are visible."""
    import rasterio
    from skimage.segmentation import find_boundaries

    rgb = np.clip(np.stack([b04, b03, b02]) * 3.5, 0, 1)
    rgb = (np.nan_to_num(rgb) * 255).astype("uint8")
    edge = find_boundaries(inside, mode="inner")
    for ch, v in enumerate((255, 220, 0)):
        rgb[ch][edge] = v
    big = rgb.repeat(scale, axis=1).repeat(scale, axis=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="PNG", width=big.shape[2], height=big.shape[1], count=3, dtype="uint8") as dst:
        dst.write(big)


# ------------------------------------------------------------------ main

def body(bbox, res, data, evalscript) -> dict:
    return {"input": {"bounds": {"bbox": bbox, "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/32643"}}, "data": [data]},
            "output": {"resx": res, "resy": res, "responses": [{"identifier": "default", "format": {"type": "image/tiff"}}]},
            "evalscript": evalscript}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start")
    ap.add_argument("--to", dest="end")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--rebuild", action="store_true", help="ignore the CSV and redo every acquisition (after the box changes)")
    ap.add_argument("--sensors", default="s1,s2")
    a = ap.parse_args()

    from cdse import Cdse
    from pyproj import Transformer
    from rasterio import features as rf
    from rasterio.io import MemoryFile
    from s1_reservoir import group_passes

    s = settings()
    g, status = box()
    res = 10
    bbox, transform, hw = grid(g, res)
    inside = rf.rasterize([(g, 1)], out_shape=hw, transform=transform).astype(bool)
    t = Transformer.from_crs(UTM, "EPSG:4326", always_xy=True)
    (lon0, lat0), (lon1, lat1) = t.transform(bbox[0], bbox[1]), t.transform(bbox[2], bbox[3])
    ll = [lon0, lat0, lon1, lat1]

    existing = [] if a.rebuild else read_csv()
    done = {(r["acquired_at"], r["sensor"]) for r in existing}
    last = max((r["acquired_at"] for r in existing), default=None)
    start = a.start or ((datetime.fromisoformat(last[:10]) - timedelta(days=3)).strftime("%Y-%m-%d") if last else "2024-01-01")
    end = a.end or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"box {int(inside.sum())} px at {res} m ({status}); {start}..{end}")

    c, rows, t0 = Cdse(), [], time.time()
    now = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    base = {"box_status": status}

    if "s1" in a.sensors:
        passes = group_passes(c.catalog("sentinel-1-grd", ll, f"{start}T00:00:00Z", f"{end}T23:59:59Z"))
        passes = [p for p in passes if (p["first"].strftime("%Y-%m-%dT%H:%M:%SZ"), "s1") not in done]
        print(f"s1: {len(passes)} new passes")
        for p in passes:
            acq = p["first"].strftime("%Y-%m-%dT%H:%M:%SZ")
            data = {"type": "sentinel-1-grd",
                    "dataFilter": {"timeRange": {"from": (p["first"] - timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                 "to": (p["last"] + timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ")},
                                   "acquisitionMode": "IW", "polarization": "DV", "orbitDirection": "DESCENDING", "resolution": "HIGH"},
                    "processing": {"backCoeff": "GAMMA0_TERRAIN", "orthorectify": True, "demInstance": "COPERNICUS_30",
                                   "speckleFilter": {"type": "LEE", "windowSizeX": 5, "windowSizeY": 5}}}
            try:
                tif = c.process(body(bbox, res, data, S1_EVAL), f"s1 {acq}")
            except RuntimeError as e:
                print(f"   s1 {acq}: skipped ({e})"); continue
            with MemoryFile(tif) as mf, mf.open() as src:
                arr = src.read().astype("float32")
            m = s1_metrics(arr[0], arr[1], arr[2] > 0, inside, s["dark_db"]["value"], s["min_valid_fraction"]["value"])
            rows.append({**base, **m, "acquired_at": acq, "sensor": "s1", "rel_orbit": p["rel_orbit"], "platform": p["platform"],
                         "product_ids": ";".join(p["ids"]), "processed_at": now()})
            print(f"   s1 {acq}: VV {m.get('vv_db')} VH {m.get('vh_db')} dark {m.get('dark_frac')} {m['quality']} {m['why']}")

    latest_clear = None
    if "s2" in a.sensors:
        days = s2_days(c.catalog("sentinel-2-l2a", ll, f"{start}T00:00:00Z", f"{end}T23:59:59Z"))
        days = [d for d in days if (d["first"].strftime("%Y-%m-%dT%H:%M:%SZ"), "s2") not in done]
        print(f"s2: {len(days)} new days")
        for d in days:
            acq = d["first"].strftime("%Y-%m-%dT%H:%M:%SZ")
            day0 = d["first"].replace(hour=0, minute=0, second=0)
            data = {"type": "sentinel-2-l2a", "dataFilter": {"timeRange": {"from": day0.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                                           "to": (day0 + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")},
                                                             "mosaickingOrder": "leastCC"}}
            try:
                tif = c.process(body(bbox, res, data, S2_EVAL), f"s2 {acq[:10]}")
            except RuntimeError as e:
                print(f"   s2 {acq}: skipped ({e})"); continue
            with MemoryFile(tif) as mf, mf.open() as src:
                b02, b03, b04, b08, b11, scl, dm = src.read().astype("float32")
            m = s2_metrics(b03, b08, b11, scl, dm, inside, s["invalid_scl"], s["min_valid_fraction"]["value"],
                           s["mndwi_threshold"]["value"], s["ndwi_threshold"]["value"])
            rows.append({**base, **m, "acquired_at": acq, "sensor": "s2", "platform": d["platform"],
                         "product_ids": ";".join(d["ids"]), "processed_at": now()})
            print(f"   s2 {acq[:10]}: water {m.get('water_frac')} ndwi {m.get('ndwi_frac')} valid {m['valid_fraction']} {m['quality']} {m['why']}")
            if m["quality"] == "ok":
                latest_clear = (acq, b02, b03, b04)

    if latest_clear:
        save_preview(latest_clear[1], latest_clear[2], latest_clear[3], inside, PREVIEW)
        print(f"preview: {PREVIEW.name} from {latest_clear[0][:10]}")
    keep = {(r["acquired_at"], r["sensor"]) for r in rows}
    write_csv([r for r in existing if (r["acquired_at"], r["sensor"]) not in keep] + rows)
    ok = sum(r["quality"] == "ok" for r in rows)
    print(f"rows={len(rows)} ok={ok} PU={c.pu:.0f} requests={c.requests} secs={time.time() - t0:.0f}")
    if a.send and rows:
        from measurement import send
        try:
            print(f"sent {send([m for r in rows for m in to_measurements({k: ('' if v is None else str(v)) for k, v in r.items()})])} rows")
        except Exception as e:  # the CSV is the record; resend later
            print(f"WARNING: send failed ({type(e).__name__}: {str(e)[:200]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
