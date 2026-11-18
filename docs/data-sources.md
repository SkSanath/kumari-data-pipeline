# Data sources: Thirparappu water estimate

Spec: `SATELLITE_WATER_SPEC.md` (18 Nov 2026). Endpoints and product names below are **as written in the spec and not
yet re-checked against the official docs**. Each one is checked, and this file updated, when its job is built.

| Source | Use | Licence / terms | Endpoint (to verify) | Checked |
|---|---|---|---|---|
| Copernicus Sentinel-1 GRD (CDSE) | Reservoir area, river width, falls box radar | Free, commercial OK. Credit: "Contains modified Copernicus Sentinel data [year]" | Token: `https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token`; Process API: `https://sh.dataspace.copernicus.eu/api/v1/process`; Catalog API first | not yet |
| Copernicus Sentinel-2 L2A (CDSE) | Falls box water cover on clear days | Same as above | Same | not yet |
| Copernicus DEM GLO-30 | Catchment zones A and B (one-off) | Free, commercial OK | to find | not yet |
| NASA GPM IMERG Early daily V07 (`GPM_3IMERGDE`) | Live catchment rain | NASA data: no restrictions; cite | GES DISC, via OPeNDAP or Harmony subsetting | not yet |
| NASA GPM IMERG Final daily V07 (`GPM_3IMERGDF`) | Backfill and backtest rain | Same | Same | not yet |
| TN-SMART daily reservoir PDF | Pechiparai, Perunchani, Chittar I/II depth, storage, %: **private calibration only** | **Licence not stated.** Not committed here, not shown on the site until the owner records written permission (site repo `docs/licences.md`, `TNSMART_PUBLIC`) | `https://beta-tnsmart.rimes.int/DATA/Rainfall/daily_reservoir/reservoir_DD-MM-YYYY.pdf` (all hits so far; the `YYYY-MM-DD` pattern is still tried) | 18 Nov 2026: archive starts Jan 2025 (no 2023-2024 files); gap about Apr to mid-Jun 2025; parser checked on 6 bulletins (Jan 2025 to Nov 2026) |

**Banned** (spec §2.3): Google Earth Engine, Open-Meteo free API, Google News RSS, public Overpass/Nominatim/OSM tiles,
Google Maps, paid imagery.

## Secrets needed (GitHub → Settings → Secrets)
- `CDSE_CLIENT_ID`, `CDSE_CLIENT_SECRET`: OAuth client from the Copernicus Data Space dashboard (Sentinel Hub section).
- `EARTHDATA_TOKEN`: NASA Earthdata login token.
- `INGEST_URL`, `INGEST_HMAC_KEY`: already set (marine).
