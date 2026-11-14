"""Copernicus Marine waves and sea level for Kanniyakumari (spec 8: marine.copernicus). GitHub Actions, every 6 hours.

Credit (required wherever shown): "Generated using E.U. Copernicus Marine Service Information".
Needs a free Copernicus Marine account: secrets COPERNICUSMARINE_USERNAME / COPERNICUSMARINE_PASSWORD.

NOT YET RUN: written without an account. On the first run, check the log: it prints the dataset variables and the grid
cells it picked. Dataset ids and the sea-level variable can be changed with environment variables, without code edits.
"""
import math, os, sys
from datetime import datetime, timedelta, timezone

import copernicusmarine
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))
from post_ingest import post  # noqa: E402

# Shore positions copied from the site's places dataset (OpenStreetMap, via data/places in the site repo).
# The script then picks a sea grid cell 2-5 km out from each, where the model has values.
TARGETS = [
    {"id": "cape-offshore", "lat": 8.0781048, "lng": 77.5553579},  # Vivekananda Rock Memorial
    {"id": "west-offshore", "lat": 8.2227132, "lng": 77.1821532},  # Thengapattanam beach
]
WAVES = os.environ.get("CMEMS_WAVES_DATASET", "cmems_mod_glo_wav_anfc_0.083deg_PT3H-i")
WAVE_VARS = {"hs": "VHM0", "swell": "VHM0_SW1"}
SEA_LEVEL = os.environ.get("CMEMS_SEALEVEL_DATASET", "cmems_mod_glo_phy_anfc_merged-sl_PT1H-i")
SEA_LEVEL_VAR = os.environ.get("CMEMS_SEALEVEL_VAR", "zos")
BBOX = dict(minimum_longitude=76.9, maximum_longitude=77.9, minimum_latitude=7.7, maximum_latitude=8.5)
MIN_KM, MAX_KM = 2.0, 5.0


def km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def open_ds(dataset_id, variables, start, end):
    return copernicusmarine.open_dataset(
        dataset_id=dataset_id, variables=variables, start_datetime=start.isoformat(), end_datetime=end.isoformat(),
        username=os.environ.get("COPERNICUSMARINE_USERNAME"), password=os.environ.get("COPERNICUSMARINE_PASSWORD"), **BBOX,
    )


def pick_cell(da, lat, lng):
    """Nearest grid cell 2-5 km from the shore point that has values (land cells are all-NaN)."""
    has = da.notnull().any(dim="time").values
    lats, lngs = da["latitude"].values, da["longitude"].values
    best = None
    for i, la in enumerate(lats):
        for j, lo in enumerate(lngs):
            if not has[i, j]:
                continue
            d = km(lat, lng, float(la), float(lo))
            score = d if MIN_KM <= d <= MAX_KM else d + 100  # prefer the 2-5 km band, else the nearest sea cell
            if best is None or score < best[0]:
                best = (score, i, j, d)
    return best


def iso(t):
    return np.datetime_as_string(t, unit="s") + "Z"


def num(v):
    v = float(v)
    return None if math.isnan(v) else round(v, 2)


def main():
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start, end = now - timedelta(hours=24), now + timedelta(hours=48)
    points = {t["id"]: {"id": t["id"], "series": {}} for t in TARGETS}
    datasets = []

    w = open_ds(WAVES, list(WAVE_VARS.values()), start, end)
    print("waves variables:", list(w.data_vars))
    datasets.append(WAVES)
    for t in TARGETS:
        cell = pick_cell(w[WAVE_VARS["hs"]], t["lat"], t["lng"])
        if cell is None:
            print(f"{t['id']}: no sea cell with wave values in the box", file=sys.stderr)
            continue
        _, i, j, d = cell
        p = points[t["id"]]
        p["lat"], p["lng"] = round(float(w["latitude"][i]), 4), round(float(w["longitude"][j]), 4)
        print(f"{t['id']}: wave cell {p['lat']},{p['lng']} at {d:.1f} km")
        for k, var in WAVE_VARS.items():
            for tt, v in zip(w["time"].values, w[var][:, i, j].values):
                p["series"].setdefault(iso(tt), {"t": iso(tt)})[k] = num(v)

    try:
        s = open_ds(SEA_LEVEL, [SEA_LEVEL_VAR], start, end)
        print("sea level variables:", list(s.data_vars))
        datasets.append(SEA_LEVEL)
        for t in TARGETS:
            cell = pick_cell(s[SEA_LEVEL_VAR], t["lat"], t["lng"])
            if cell is None:
                continue
            _, i, j, d = cell
            print(f"{t['id']}: sea-level cell at {d:.1f} km")
            for tt, v in zip(s["time"].values, s[SEA_LEVEL_VAR][:, i, j].values):
                points[t["id"]]["series"].setdefault(iso(tt), {"t": iso(tt)})["sl"] = num(v)
    except Exception as e:  # sea level is optional: waves still go up
        print("sea level skipped:", e, file=sys.stderr)

    out = [{"id": p["id"], "lat": p["lat"], "lng": p["lng"], "series": sorted(p["series"].values(), key=lambda r: r["t"])}
           for p in points.values() if "lat" in p]
    if not out:
        sys.exit("no points: nothing to send")
    payload = {"kind": "marine", "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "datasets": datasets, "points": out}
    print("posted:", post("marine", payload))


if __name__ == "__main__":
    main()
