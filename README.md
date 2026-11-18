# kumari-data-pipeline

Public repo (GitHub Actions). Phase 0: keep-alive + signed test ping. Move this folder to its own public GitHub repo; it contains no secrets.


## Marine (Copernicus Marine, spec 8)
- `marine/fetch_marine.py` runs every 6 hours (`.github/workflows/marine.yml`): waves (`VHM0`, `VHM0_SW1`) and sea level near the cape and the west coast, POSTed signed to `/api/ingest/marine`.
- Secrets: `COPERNICUSMARINE_USERNAME`, `COPERNICUSMARINE_PASSWORD` (free account at marine.copernicus.eu), `INGEST_URL` (e.g. `https://<domain>/api/ingest`), `INGEST_HMAC_KEY`.
- Backtest history: run the workflow by hand with "history" ticked, download the `ferry-waves-daily` artifact, put it in the site repo at `data/backtest/ferry-waves-daily.json`, then `npm run backtest:ferry`.
- First run with an account: 18 Nov 2026. On the first run, read the log: dataset variables, the grid cells picked, and whether sea level was found (`CMEMS_SEALEVEL_VAR`, default `total_sea_level`, which includes the tide).
- Credit wherever shown: "Generated using E.U. Copernicus Marine Service Information".

## Thirparappu water estimate (SATELLITE_WATER_SPEC.md)
- Signed measurements go to `/api/ingest/signals` with `common/measurement.py` (`measurement(...)` checks each row; `send(rows)`).
- `config/processing.yaml`: pipeline-only settings (Otsu clamps, S2 masks, TN-SMART URL patterns). Rule thresholds live in the site repo (`config/falls-thresholds.yaml`).
- `aoi/*.geojson`: placeholders (`"status": "draft"`) until the owner draws them. Do not invent coordinates.
- `docs/events.csv`: dated past events for the backtest (public-safe). `docs/data-sources.md`: sources, licences, endpoints.
- Tests: `.github/workflows/tests.yml` runs `pytest` (Python 3.11, `requirements-water.txt`).
- **TN-SMART PDFs and their numbers are never committed here** (licence not stated).
- **TN-SMART** (`tnsmart/`): `parse.py` reads the four Kanniyakumari dams from the bulletin text and refuses rows whose numbers disagree; `fetch_parse.py` fetches, parses and sends rows marked private (`--from/--to` for backfill, `--dry-run`). Workflow `tnsmart.yml` is manual until the site route is deployed. For local real-PDF tests, put bulletins in the git-ignored `tnsmart_cache/` as `YYYY-MM-DD.pdf`.
- **Reservoir area** (`sat/s1_reservoir.py`, workflow `s1-reservoir.yml`, daily): water area of Pechiparai and Chittar I/II per Sentinel-1 pass into `data/s1_reservoir_area.csv`. `resend_all` sends the whole series to the site (e.g. after deploy). Outlines from `sat/derive_aois.py` (owner-checked).
