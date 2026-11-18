"""Reservoir water area from Sentinel-1 for Pechiparai, Chittar I and Chittar II (SATELLITE_WATER_SPEC.md §7.1, step 5).

For every descending IW pass over the three reservoirs: one small 10 m GeoTIFF of terrain-flattened gamma0 VV (dB,
Lee 5x5) covering all three outlines. Then per reservoir, inside its owner-checked outline:
  - Otsu threshold on that pass's histogram, clamped to config (-22..-14 dB); weak separation -> quality "poor";
  - water area = water pixels x pixel area;
  - jump above max_jump_pct from the previous pass in the same relative orbit -> "suspect" (rain not checked yet);
  - pass covering less than min_valid_fraction of the outline -> stored as missing.

Results are appended to data/s1_reservoir_area.csv (public: derived Copernicus data) and, with --send, POSTed to the site.

  python sat/s1_reservoir.py                         # new passes since the last row in the CSV (daily)
  python sat/s1_reservoir.py --from 2024-01-01       # backfill; already processed passes are skipped
  python sat/s1_reservoir.py --send                  # also send the new rows to the site
  python sat/s1_reservoir.py --resend-all            # send every CSV row (upsert; e.g. after the site route is deployed)

Credit: "Contains modified Copernicus Sentinel data [year]".
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sat"))
sys.path.insert(0, str(ROOT / "common"))

UTM = "EPSG:32643"
CSV_PATH = ROOT / "data" / "s1_reservoir_area.csv"
FIELDS = ["acquired_at", "place", "area_km2", "quality", "why", "otsu_db", "threshold_db", "separation_db",
          "valid_fraction", "water_px", "res_m", "rel_orbit", "orbit", "platform", "product_ids", "processed_at"]
SOURCE = "Copernicus Sentinel-1 GRD"

EVALSCRIPT = """//VERSION=3
function setup() {
  return { input: [{ bands: ["VV", "dataMask"] }], output: { bands: 2, sampleType: "FLOAT32" } };
}
function evaluatePixel(s) {
  if (!s.dataMask || !(s.VV > 0)) return [NaN, 0];
  return [10 * Math.log10(s.VV), 1];
}"""


@dataclass
class Settings:
    clamp_min: float
    clamp_max: float
    min_separation: float
    min_valid: float
    max_jump_pct: float
    res_m: float
    outlines: dict[str, str]


def settings() -> Settings:
    c = yaml.safe_load((ROOT / "config" / "processing.yaml").read_text())
    s1, r = c["s1_water"], c["s1_reservoir"]
    return Settings(s1["otsu_clamp_db"]["min"], s1["otsu_clamp_db"]["max"], s1["min_separation_db"]["value"],
                    r["min_valid_fraction"]["value"], r["max_jump_pct"]["value"], r["res_m"], r["reservoirs"])


# ------------------------------------------------------------------ the measurement (pure, tested)

def measure(db: np.ndarray, valid: np.ndarray, inside: np.ndarray, res_m: float, s: Settings) -> dict:
    """Water area inside one outline for one pass. db: VV in dB; valid: pass has data; inside: outline mask."""
    from skimage.filters import threshold_otsu

    n_in = int(inside.sum())
    use = inside & valid & np.isfinite(db)
    frac = float(use.sum()) / n_in if n_in else 0.0
    out = {"valid_fraction": round(frac, 4), "res_m": res_m}
    if frac < s.min_valid:
        return {**out, "area_km2": None, "quality": "missing", "why": f"pass covers {frac:.0%} of the outline"}
    v = db[use]
    t = float(threshold_otsu(v))
    lo, hi = v[v < t], v[v >= t]
    sep = float(hi.mean() - lo.mean()) if len(lo) and len(hi) else 0.0
    thr = min(max(t, s.clamp_min), s.clamp_max)
    water = int((v < thr).sum())
    out.update(otsu_db=round(t, 2), threshold_db=round(thr, 2), separation_db=round(sep, 2), water_px=water,
               area_km2=round(water * res_m * res_m / 1e6, 4))
    if sep < s.min_separation:
        return {**out, "quality": "poor", "why": f"histogram not bimodal (classes {sep:.1f} dB apart)"}
    if thr != t:
        return {**out, "quality": "suspect", "why": f"Otsu {t:.1f} dB outside {s.clamp_min}..{s.clamp_max}, clamped"}
    return {**out, "quality": "ok", "why": ""}


def jump_check(row: dict, previous: dict | None, max_jump_pct: float) -> dict:
    """Mark a big change from the previous same-orbit value as suspect (rain is not checked yet)."""
    if row["quality"] != "ok" or not previous or previous.get("area_km2") in (None, ""):
        return row
    prev = float(previous["area_km2"])
    if prev > 0 and abs(row["area_km2"] - prev) / prev * 100 > max_jump_pct:
        pct = (row["area_km2"] - prev) / prev * 100
        return {**row, "quality": "suspect", "why": f"{pct:+.0f}% since the previous pass in this orbit (rain not checked)"}
    return row


def group_passes(features: list[dict]) -> list[dict]:
    """Catalog features -> one entry per descending pass (slices of the same pass are minutes apart)."""
    feats = [f for f in features if str(f["properties"].get("sat:orbit_state", "")).lower() == "descending"
             and f["properties"].get("sar:instrument_mode", "IW") == "IW"]
    feats.sort(key=lambda f: f["properties"]["datetime"])
    passes: list[dict] = []
    for f in feats:
        p = f["properties"]
        t = datetime.fromisoformat(p["datetime"].replace("Z", "+00:00"))
        if passes and (t - passes[-1]["last"]) <= timedelta(minutes=5) and passes[-1]["platform"] == p.get("platform"):
            passes[-1]["ids"].append(f["id"])
            passes[-1]["last"] = t
            continue
        passes.append({"first": t, "last": t, "ids": [f["id"]], "rel_orbit": p.get("sat:relative_orbit"),
                       "orbit": p.get("sat:orbit_state"), "platform": p.get("platform")})
    return passes


# ------------------------------------------------------------------ geometry

def outlines_utm(s: Settings) -> dict:
    from pyproj import Transformer
    from shapely.geometry import shape
    from shapely.ops import transform as tr

    t = Transformer.from_crs("EPSG:4326", UTM, always_xy=True)
    out = {}
    for place, rel in s.outlines.items():
        j = json.loads((ROOT / rel).read_text())
        if j.get("status") != "owner-checked" or not j["features"]:
            sys.exit(f"{rel}: outline not owner-checked; refusing to measure")
        out[place] = tr(lambda x, y, z=None: t.transform(x, y), shape(j["features"][0]["geometry"]))
    return out


def grid(polys: dict, res: float):
    from rasterio.transform import from_origin
    from shapely.ops import unary_union

    minx, miny, maxx, maxy = unary_union(list(polys.values())).bounds
    minx, miny = math.floor(minx / res) * res - res, math.floor(miny / res) * res - res
    maxx, maxy = math.ceil(maxx / res) * res + res, math.ceil(maxy / res) * res + res
    w, h = int(round((maxx - minx) / res)), int(round((maxy - miny) / res))
    return [minx, miny, maxx, maxy], from_origin(minx, maxy, res, res), (h, w)


# ------------------------------------------------------------------ CSV store

def read_csv() -> list[dict]:
    if not CSV_PATH.exists():
        return []
    with CSV_PATH.open(newline="") as f:
        return list(csv.DictReader(f))


def append_csv(rows: list[dict]) -> None:
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    new = not CSV_PATH.exists()
    with CSV_PATH.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        if new:
            w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") if r.get(k) is not None else "" for k in FIELDS})


def to_measurement(r: dict) -> dict:
    from measurement import measurement

    t = datetime.fromisoformat(r["acquired_at"].replace("Z", "+00:00"))
    area = None if r["area_km2"] in ("", None) else float(r["area_km2"])
    ids = [i for i in str(r["product_ids"]).split(";") if i]
    url = f"https://catalogue.dataspace.copernicus.eu/odata/v1/Products?$filter=Name%20eq%20'{ids[0]}'" if ids else "https://dataspace.copernicus.eu/"
    meta = {k: r[k] for k in ("orbit", "rel_orbit", "platform", "otsu_db", "threshold_db", "separation_db", "valid_fraction", "water_px", "res_m", "why") if r.get(k) not in ("", None)}
    meta["credit"] = f"Contains modified Copernicus Sentinel data {t.year}"
    return measurement(signal="reservoir_area_km2", place=r["place"], value=area, unit="km2", observed_at=t,
                       source=SOURCE, source_url=url, product_id=";".join(ids)[:200] or None,
                       quality=r["quality"] if area is not None else "missing", visibility="public", meta=meta,
                       fetched_at=datetime.fromisoformat(r["processed_at"].replace("Z", "+00:00")))


def send_rows(rows: list[dict]) -> None:
    from measurement import send

    try:
        n = send([to_measurement(r) for r in rows])
        print(f"sent {n} rows to the site")
    except Exception as e:  # the CSV is the record; a failed send can be repeated with --resend-all
        print(f"WARNING: send to the site failed ({type(e).__name__}: {str(e)[:200]}); rows are kept in the CSV")


# ------------------------------------------------------------------ main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start")
    ap.add_argument("--to", dest="end")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--resend-all", action="store_true")
    ap.add_argument("--max-passes", type=int, default=400)
    a = ap.parse_args()

    existing = read_csv()
    if a.resend_all:
        send_rows(existing)
        return 0

    from cdse import Cdse
    from rasterio import features as rf
    from rasterio.io import MemoryFile
    from pyproj import Transformer

    s = settings()
    polys = outlines_utm(s)
    bbox, transform, shape_hw = grid(polys, s.res_m)
    inside = {p: rf.rasterize([(g, 1)], out_shape=shape_hw, transform=transform).astype(bool) for p, g in polys.items()}

    done = {r["acquired_at"] for r in existing}
    last = max((r["acquired_at"] for r in existing), default=None)
    start = a.start or (last[:10] if last else "2024-01-01")
    end = a.end or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    t = Transformer.from_crs(UTM, "EPSG:4326", always_xy=True)
    lon0, lat0 = t.transform(bbox[0], bbox[1])
    lon1, lat1 = t.transform(bbox[2], bbox[3])

    c = Cdse()
    feats = c.catalog("sentinel-1-grd", [lon0, lat0, lon1, lat1], f"{start}T00:00:00Z", f"{end}T23:59:59Z")
    passes = [p for p in group_passes(feats) if p["first"].strftime("%Y-%m-%dT%H:%M:%SZ") not in done]
    print(f"{start}..{end}: {len(feats)} catalog items, {len(passes)} new descending passes")
    passes = passes[: a.max_passes]

    # Previous same-orbit value per place, for the jump check.
    prev: dict[tuple, dict] = {}
    for r in sorted(existing, key=lambda r: r["acquired_at"]):
        if r["quality"] in ("ok", "suspect") and r["area_km2"]:
            prev[(r["place"], r["rel_orbit"])] = r

    t0, new_rows = time.time(), []
    for p in passes:
        acq = p["first"].strftime("%Y-%m-%dT%H:%M:%SZ")
        body = {
            "input": {
                "bounds": {"bbox": bbox, "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/32643"}},
                "data": [{
                    "type": "sentinel-1-grd",
                    "dataFilter": {"timeRange": {"from": (p["first"] - timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                 "to": (p["last"] + timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ")},
                                   "acquisitionMode": "IW", "polarization": "DV", "orbitDirection": "DESCENDING", "resolution": "HIGH"},
                    "processing": {"backCoeff": "GAMMA0_TERRAIN", "orthorectify": True, "demInstance": "COPERNICUS_30",
                                   "speckleFilter": {"type": "LEE", "windowSizeX": 5, "windowSizeY": 5}},
                }],
            },
            "output": {"resx": s.res_m, "resy": s.res_m, "responses": [{"identifier": "default", "format": {"type": "image/tiff"}}]},
            "evalscript": EVALSCRIPT,
        }
        try:
            tif = c.process(body, acq)
        except RuntimeError as e:
            print(f"   {acq}: skipped ({e}); will be retried next run")
            continue
        with MemoryFile(tif) as mf, mf.open() as src:
            arr = src.read().astype("float32")
        if arr.shape[1:] != shape_hw:
            print(f"   {acq}: unexpected size {arr.shape[1:]} vs {shape_hw}; skipped")
            continue
        db, valid = arr[0], arr[1] > 0
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for place in polys:
            m = measure(db, valid, inside[place], s.res_m, s)
            row = {"acquired_at": acq, "place": place, **m, "rel_orbit": p["rel_orbit"], "orbit": p["orbit"],
                   "platform": p["platform"], "product_ids": ";".join(p["ids"]), "processed_at": now}
            row = jump_check(row, prev.get((place, str(p["rel_orbit"]))), s.max_jump_pct)
            if row["quality"] in ("ok", "suspect") and row["area_km2"] is not None:
                prev[(place, str(p["rel_orbit"]))] = {k: str(v) for k, v in row.items()}
            new_rows.append(row)
            print(f"   {acq} {place}: {row['area_km2']} km2 {row['quality']} {row['why']}")
        append_csv([r for r in new_rows if r["acquired_at"] == acq])  # save as we go

    print(f"passes={len(passes)} rows={len(new_rows)} PU={c.pu:.0f} requests={c.requests} secs={time.time() - t0:.0f}")
    if a.send and new_rows:
        send_rows([{k: ("" if v is None else str(v)) for k, v in r.items()} for r in new_rows])
    return 0


if __name__ == "__main__":
    sys.exit(main())
