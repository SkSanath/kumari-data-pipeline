# Data sources: Thirparappu water estimate

Spec: `SATELLITE_WATER_SPEC.md` (18 Nov 2026). Endpoints and product names below are **as written in the spec and not
yet re-checked against the official docs**. Each one is checked, and this file updated, when its job is built.

| Source | Use | Licence / terms | Endpoint (to verify) | Checked |
|---|---|---|---|---|
| Copernicus Sentinel-1 GRD (CDSE) | Reservoir area, river width, falls box radar | Free, commercial OK. Credit: "Contains modified Copernicus Sentinel data [year]" | Token: `https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token`; Process API: `https://sh.dataspace.copernicus.eu/api/v1/process`; Catalog API first | 18 Nov 2026: token and Catalog OK. Over the falls: S1 only **descending, relative orbit 165**, IW, VV+VH, about 00:40 UTC, **every 12 days** (21 Oct, 2 Nov, 14 Nov 2026) |
| Copernicus Sentinel-2 L2A (CDSE) | Falls box water cover on clear days | Same as above | Same | 18 Nov 2026: 6 scenes in 30 days, cloud 30-97% (monsoon) |
| Copernicus DEM GLO-30 (public instance) | Catchment zones A and B (one-off) | Licence COP-DEM-GLO-30-F "Full, Free & Open" (ESA): reproduction, distribution, public communication and modification, free of charge, no purpose limit. Notice for modified data: "produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved", plus the no-liability sentence when redistributing | AWS Open Data `copernicus-dem-30m` (COG tiles, no account). The CDSE Process API refused DEM requests on our free account (HTTP 403) | 18 Nov 2026: tile N08 E077 read |
| NASA GPM IMERG Early daily V07 (`GPM_3IMERGDE`) | Live catchment rain | NASA data: no restrictions; cite | GES DISC cloud OPeNDAP (`opendap.earthdata.nasa.gov`, DAP4 `.dmr` / `.dap.csv` subsets; DAP2 `.dds` answers 400) | 18 Nov 2026: V07C, layout (time, lon, lat), latest granule = yesterday |
| NASA GPM IMERG Final daily V07 (`GPM_3IMERGDF`) | Backfill and backtest rain | Same | Same | 18 Nov 2026: V07 in CMR; **latest granule 30 Sep 2025** (Final lags ~13 months), so later backtest days need Late or Early |
| NASA GPM IMERG Late daily V07 (`GPM_3IMERGDL`) | Backfill from Oct 2025 (Final not out yet) and live after Early | Same | Same | 18 Nov 2026: V07C, latest granule = yesterday |
| TN-SMART daily reservoir PDF | Pechiparai, Perunchani, Chittar I/II depth, storage, %: **private calibration only** | **Licence not stated.** Not committed here, not shown on the site until the owner records written permission (site repo `docs/licences.md`, `TNSMART_PUBLIC`) | `https://beta-tnsmart.rimes.int/DATA/Rainfall/daily_reservoir/reservoir_DD-MM-YYYY.pdf` (all hits so far; the `YYYY-MM-DD` pattern is still tried) | 18 Nov 2026: archive starts Jan 2025 (no 2023-2024 files); gap about Apr to mid-Jun 2025; parser checked on 6 bulletins (Jan 2025 to Nov 2026) |

**Banned** (spec §2.3): Google Earth Engine, Open-Meteo free API, Google News RSS, public Overpass/Nominatim/OSM tiles,
Google Maps, paid imagery.

## Secrets needed (GitHub → Settings → Secrets)
- `CDSE_CLIENT_ID`, `CDSE_CLIENT_SECRET`: OAuth client from the Copernicus Data Space dashboard (Sentinel Hub section).
- `EARTHDATA_TOKEN`: NASA Earthdata login token.
- `INGEST_URL`, `INGEST_HMAC_KEY`: already set (marine).

## Areas of interest (18 Nov 2026)
Derived from a Sentinel-1 water-frequency map, not drawn by hand: see `aoi/README.md`. Cost: 551 + 656 processing
units for two years of descending passes (free plan has a per-minute limit; the script waits and retries on 429).
**The Kodayar between Pechiparai and the falls is not visible to Sentinel-1 at 10 m**, so the river-width signal
(spec §7.2) cannot be built from it as specified.

## Reservoir area series (step 5, 18 Nov 2026)
- `sat/s1_reservoir.py` → `data/s1_reservoir_area.csv` (public, derived Copernicus data). Backfill 2024-01-10 to
  2026-11-14: 85 descending passes (relative orbit 165; Sentinel-1A, and Sentinel-1D from 2026), 255 rows, 1,160 PU.
- Every pass cleanly bimodal (classes ≥ 6.9 dB apart). Otsu clamp set to −18..−10 dB after the first run showed the
  spec's −22..−14 example does not fit terrain-flattened gamma0 (Otsu −15.9..−10.7). 251 rows ok, 4 suspect (rises of
  16–20% at monsoon onset; rain not checked yet).
- Private sanity check against 6 TN-SMART bulletins (numbers not recorded here): area rises with depth, but **near full
  the area hardly changes**, so a depth estimate around the 42 ft mark will be uncertain. Step 6 measures this.
- Daily run 09:00 UTC; sending to the site fails softly until `/api/ingest/signals` is deployed, then
  run the workflow once with `resend_all`.
