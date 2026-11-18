"""NASA GPM IMERG daily rain over catchment zones A and B (SATELLITE_WATER_SPEC.md §6.2, build step 7).

- Products (V07, GES DISC): Early `GPM_3IMERGDE` (live, ~4 h), Late `GPM_3IMERGDL` (~14 h), Final `GPM_3IMERGDF`
  (months behind). Each run is stored as its own source, so Final never silently overwrites Early; the site prefers
  Final > Late > Early for the same day.
- Only the 0.1 degree cells over the zones are read, through the granule's OPeNDAP subset link (never a global file).
- Zone rain = mean of the cells weighted by the share of each cell inside the zone (areas in UTM 43N).
- A day is UTC (IMERG's day). observed_at = 00:00 UTC of that day, meta.period = "P1D". A fill value in any cell that
  covers the zone makes that zone-day "missing", never a guess.

  python rain/imerg.py --probe                          # print links and variable layout for one granule
  python rain/imerg.py                                  # live: Early and Late, last 7 days
  python rain/imerg.py --run final --from 2024-01-01 --to 2025-09-30
  python rain/imerg.py --run late  --from 2025-10-01
  add --dry-run to send nothing; numbers are public NASA data (no licence limit), so they may be logged.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))

CMR = "https://cmr.earthdata.nasa.gov/search"
UA = {"User-Agent": "KumariLivePipeline/0.1"}
RUNS = {
    "early": ("GPM_3IMERGDE", "NASA GPM IMERG Early daily V07"),
    "late": ("GPM_3IMERGDL", "NASA GPM IMERG Late daily V07"),
    "final": ("GPM_3IMERGDF", "NASA GPM IMERG Final daily V07"),
}
ZONES = {"zone_a": "catchment_zone_a.geojson", "zone_b": "catchment_zone_b.geojson"}
STEP = 0.1  # IMERG grid: cell centres at -179.95 + 0.1 i (lon), -89.95 + 0.1 j (lat)
VAR = "precipitation"


# ---------------------------------------------------------------- grid weights (pure, tested)

def cell_index(lon: float, lat: float) -> tuple[int, int]:
    return int(math.floor((lon + 180) / STEP + 1e-9)), int(math.floor((lat + 90) / STEP + 1e-9))


def cell_box(i: int, j: int):
    from shapely.geometry import box

    return box(-180 + i * STEP, -90 + j * STEP, -180 + (i + 1) * STEP, -90 + (j + 1) * STEP)


def weights(zone_wgs84) -> dict[tuple[int, int], float]:
    """{(lon_idx, lat_idx): share of the zone's area in that cell}, summing to 1."""
    from pyproj import Transformer
    from shapely.ops import transform as tr

    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32643", always_xy=True).transform
    zu = tr(to_utm, zone_wgs84)
    minx, miny, maxx, maxy = zone_wgs84.bounds
    (i0, j0), (i1, j1) = cell_index(minx, miny), cell_index(maxx, maxy)
    out = {}
    for i in range(i0, i1 + 1):
        for j in range(j0, j1 + 1):
            a = tr(to_utm, cell_box(i, j)).intersection(zu).area
            if a > 0:
                out[(i, j)] = a
    total = sum(out.values())
    return {k: v / total for k, v in out.items()}


def zone_mean(w: dict[tuple[int, int], float], grid: dict[tuple[int, int], float | None]) -> float | None:
    """Weighted mean; None if any contributing cell is missing or a fill value (< 0)."""
    s = 0.0
    for k, wt in w.items():
        v = grid.get(k)
        if v is None or v < 0 or not math.isfinite(v):
            return None
        s += wt * v
    return round(s, 2)


def parse_ascii(text: str, var: str = VAR) -> list:
    """Values of `var` from an OPeNDAP DAP2 .ascii response as a nested list (time, lon, lat) or (time, lat, lon)."""
    body = text.split("---------------------------------------------", 1)[-1]
    rows: dict[tuple[int, ...], list[float]] = {}
    for line in body.splitlines():
        m = re.match(rf"\s*(?:{re.escape(var)}\.{re.escape(var)}|{re.escape(var)})?((?:\[\d+\])+),\s*(.*)$", line)
        if not m:
            continue
        idx = tuple(int(x) for x in re.findall(r"\[(\d+)\]", m.group(1)))
        rows[idx] = [float(v) for v in m.group(2).split(",") if v.strip()]
    if not rows:
        raise ValueError("no values in OPeNDAP response")
    return [rows[k] for k in sorted(rows)]


# ---------------------------------------------------------------- NASA access

def session(token: str):
    from access_check import NasaSession

    s = NasaSession()
    s.headers.update({**UA, "Authorization": f"Bearer {token}"})
    return s


def granules(short: str, start: date, end: date) -> dict[date, dict]:
    """CMR granule records by UTC day for one product (public metadata, pages followed)."""
    import requests

    out: dict[date, dict] = {}
    page = 1
    while True:
        r = requests.get(f"{CMR}/granules.json", headers=UA, timeout=60, params={
            "short_name": short, "version": "07", "temporal": f"{start}T00:00:00Z,{end}T23:59:59Z",
            "page_size": 2000, "page_num": page, "sort_key": "start_date"})
        r.raise_for_status()
        es = r.json().get("feed", {}).get("entry", [])
        for e in es:
            out[date.fromisoformat(e["time_start"][:10])] = e
        if len(es) < 2000:
            return out
        page += 1


def opendap_url(entry: dict) -> str | None:
    for l in entry.get("links", []):
        h = l.get("href", "")
        if "opendap" in h.lower() and h.startswith("https://"):
            return re.sub(r"\.html$", "", h)
    # Fall back: GES DISC data URL -> its OPeNDAP twin (same path under /opendap/).
    for l in entry.get("links", []):
        h = l.get("href", "")
        if h.startswith("https://") and h.endswith(".nc4") and "/data/" in h:
            return h.replace("/data/", "/opendap/", 1)
    return None


def dims(sess, url: str) -> list[str]:
    """Dimension names of VAR from the .dds, e.g. ['time', 'lon', 'lat']."""
    r = sess.get(url + ".dds", timeout=60)
    r.raise_for_status()
    m = re.search(rf"\b{VAR}((?:\[\s*\w+\s*=\s*\d+\s*\])+)", r.text)
    if not m:
        raise ValueError(f"{VAR} not in DDS")
    return re.findall(r"\[\s*(\w+)\s*=", m.group(1))


def read_cells(sess, url: str, order: list[str], cells: set[tuple[int, int]]) -> dict[tuple[int, int], float]:
    is_, js_ = [c[0] for c in cells], [c[1] for c in cells]
    i0, i1, j0, j1 = min(is_), max(is_), min(js_), max(js_)
    sl = {"time": "[0:0]", "lon": f"[{i0}:{i1}]", "lat": f"[{j0}:{j1}]"}
    q = "".join(sl[d] for d in order)
    r = sess.get(f"{url}.ascii?{VAR}{q}", timeout=120)
    r.raise_for_status()
    rows = parse_ascii(r.text)
    grid: dict[tuple[int, int], float] = {}
    # rows are the innermost vectors in index order; innermost dim is order[-1]
    outer = [d for d in order if d != "time"]
    n_in = (i1 - i0 + 1) if outer[-1] == "lon" else (j1 - j0 + 1)
    for k, vec in enumerate(rows):
        if len(vec) != n_in:
            raise ValueError("unexpected OPeNDAP row length")
        for m, v in enumerate(vec):
            if outer[-1] == "lat":
                grid[(i0 + k, j0 + m)] = v
            else:
                grid[(i0 + m, j0 + k)] = v
    return grid


# ---------------------------------------------------------------- main

def load_zones():
    from shapely.geometry import shape

    out = {}
    for key, fn in ZONES.items():
        j = json.loads((ROOT / "aoi" / fn).read_text())
        if not j.get("features"):
            sys.exit(f"aoi/{fn} has no outline yet: derive the zones first (sat/derive_zones.py)")
        out[key] = shape(j["features"][0]["geometry"])
    return out


def main() -> int:
    from access_check import env
    from measurement import measurement, send

    ap = argparse.ArgumentParser()
    ap.add_argument("--run", choices=[*RUNS, "live"], default="live")
    ap.add_argument("--from", dest="start")
    ap.add_argument("--to", dest="end")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    token = env("EARTHDATA_TOKEN")
    if not token:
        sys.exit("EARTHDATA_TOKEN is not set")
    sess = session(token)
    today = datetime.now(timezone.utc).date()

    if a.probe:
        for run, (short, _) in RUNS.items():
            g = granules(short, today - timedelta(40 if run != "final" else 500), today)
            if not g:
                print(f"{run}: no granules"); continue
            d = max(g); e = g[d]
            print(f"{run}: {len(g)} granules, latest {d}; links:")
            for l in e.get("links", []):
                print(f"   {l.get('rel', '').split('#')[-1]:<14} {l.get('href')}")
            url = opendap_url(e)
            print(f"   opendap -> {url}")
            if url:
                print(f"   dims of {VAR}: {dims(sess, url)}")
        return 0

    zones = load_zones()
    w = {k: weights(z) for k, z in zones.items()}
    for k, wk in w.items():
        print(f"{k}: {len(wk)} cells, weights {', '.join(f'{c}:{v:.2f}' for c, v in sorted(wk.items()))}")
    cells = set().union(*[set(wk) for wk in w.values()])

    jobs = [("early", today - timedelta(7), today), ("late", today - timedelta(7), today)] if a.run == "live" else \
        [(a.run, date.fromisoformat(a.start), date.fromisoformat(a.end) if a.end else today)]
    rows, t0, order = [], time.time(), None
    fetched = datetime.now(timezone.utc)
    for run, start, end in jobs:
        short, source = RUNS[run]
        g = granules(short, start, end)
        print(f"{run}: {len(g)} granules {start}..{end}")
        n_missing = 0
        for d in sorted(g):
            url = opendap_url(g[d])
            vals = {}
            try:
                if url is None:
                    raise ValueError("no OPeNDAP link")
                order = order or dims(sess, url)
                grid = read_cells(sess, url, order, cells)
                vals = {k: zone_mean(wk, grid) for k, wk in w.items()}
            except Exception as e:  # noqa: BLE001 - one bad day is stored as missing, the run goes on
                print(f"   {d}: {type(e).__name__}: {str(e)[:120]}")
            for k in w:
                v = vals.get(k)
                n_missing += v is None
                rows.append(measurement(
                    signal="rain_mm_day", place=k, value=v, unit="mm", quality="ok" if v is not None else "missing",
                    observed_at=datetime(d.year, d.month, d.day, tzinfo=timezone.utc), fetched_at=fetched,
                    source=source, source_url=url or f"{CMR}/granules.json?short_name={short}", product_id=g[d].get("producer_granule_id") or g[d].get("title"),
                    visibility="public", meta={"period": "P1D", "day_utc": d.isoformat(), "run": run, "cells": len(w[k]),
                                               "credit": "NASA GPM IMERG V07, GES DISC"}))
        print(f"{run}: done, {n_missing} missing zone-days")
    vals = [r["value"] for r in rows if r["value"] is not None]
    print(f"rows={len(rows)} with_value={len(vals)} max_mm={max(vals) if vals else None} secs={time.time() - t0:.0f}")
    if rows and not a.dry_run:
        for k in range(0, len(rows), 1000):
            send(rows[k:k + 1000])
        print("sent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
