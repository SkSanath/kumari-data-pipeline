# kumari-data-pipeline

Public repo (GitHub Actions). Phase 0: keep-alive + signed test ping. Move this folder to its own public GitHub repo; it contains no secrets.


## Marine (Copernicus Marine, spec 8)
- `marine/fetch_marine.py` runs every 6 hours (`.github/workflows/marine.yml`): waves (`VHM0`, `VHM0_SW1`) and sea level near the cape and the west coast, POSTed signed to `/api/ingest/marine`.
- Secrets: `COPERNICUSMARINE_USERNAME`, `COPERNICUSMARINE_PASSWORD` (free account at marine.copernicus.eu), `INGEST_URL` (e.g. `https://<domain>/api/ingest`), `INGEST_HMAC_KEY`.
- Backtest history: run the workflow by hand with "history" ticked, download the `ferry-waves-daily` artifact, put it in the site repo at `data/backtest/ferry-waves-daily.json`, then `npm run backtest:ferry`.
- Not yet run (no account when written). On the first run, read the log: dataset variables, the grid cells picked, and whether sea level was found (`CMEMS_SEALEVEL_VAR`, default `zos`).
- Credit wherever shown: "Generated using E.U. Copernicus Marine Service Information".
