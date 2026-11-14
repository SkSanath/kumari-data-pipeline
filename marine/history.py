"""Daily wave history off the cape for the ferry-risk backtest (spec 12.4). Run by hand (workflow_dispatch).

Writes history/ferry-waves-daily.json: for each India day, the highest wave height and swell between 06:00 and 18:00 IST
at the cape grid cell. Copy that file into the site repo as data/backtest/ferry-waves-daily.json, then run
`npm run backtest:ferry` there.

The analysis-and-forecast dataset keeps a rolling archive; if the dates you need are older than it, set
CMEMS_HISTORY_DATASET to the multi-year (reanalysis or interim) wave dataset id shown in the Copernicus catalogue.
NOT YET RUN: written without an account. Check the printed variables and time range on the first run.
"""
import json, os, sys
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from fetch_marine import TARGETS, WAVE_VARS, open_ds, pick_cell  # noqa: E402

DATASET = os.environ.get("CMEMS_HISTORY_DATASET", "cmems_mod_glo_wav_anfc_0.083deg_PT3H-i")
# Windows around the dated events in the site's data/backtest/ferry-events.json, plus quiet months for false alarms.
RANGES = [r.split("..") for r in os.environ.get("HISTORY_RANGES", "2024-12-01..2025-01-31,2025-05-01..2025-06-30,2026-04-01..2026-05-31").split(",")]
IST = timezone(timedelta(hours=5, minutes=30))


def main():
    cape = next(t for t in TARGETS if t["id"] == "cape-offshore")
    days = {}
    for a, b in RANGES:
        start = datetime.fromisoformat(a).replace(tzinfo=timezone.utc)
        end = datetime.fromisoformat(b).replace(tzinfo=timezone.utc) + timedelta(days=1)
        ds = open_ds(DATASET, list(WAVE_VARS.values()), start, end)
        print(a, b, "variables:", list(ds.data_vars), "times:", str(ds["time"].values[0]), "to", str(ds["time"].values[-1]))
        cell = pick_cell(ds[WAVE_VARS["hs"]], cape["lat"], cape["lng"])
        if cell is None:
            sys.exit("no sea cell near the cape")
        _, i, j, d = cell
        for k, var in WAVE_VARS.items():
            for tt, v in zip(ds["time"].values, ds[var][:, i, j].values):
                if np.isnan(v):
                    continue
                t = datetime.fromtimestamp(tt.astype("datetime64[s]").astype(int), tz=timezone.utc).astimezone(IST)
                if not (6 <= t.hour <= 18):  # ferry hours, roughly; only daytime values matter
                    continue
                day = days.setdefault(t.date().isoformat(), {"day": t.date().isoformat()})
                day[k] = round(max(day.get(k, 0.0), float(v)), 2)
    os.makedirs("history", exist_ok=True)
    out = {"dataset": DATASET, "credit": "Generated using E.U. Copernicus Marine Service Information",
           "generatedAt": datetime.now(timezone.utc).isoformat(), "days": sorted(days.values(), key=lambda r: r["day"])}
    with open("history/ferry-waves-daily.json", "w") as f:
        json.dump(out, f, indent=1)
    print("days:", len(out["days"]))


if __name__ == "__main__":
    main()
