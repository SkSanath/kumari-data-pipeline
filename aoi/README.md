# Areas of interest

Derived 18 Nov 2026 by `sat/derive_aois.py` (GitHub run 44281240797). No coordinates drawn by hand: the outlines come from
a Sentinel-1 water-frequency map (60 descending passes, 2024-01-01..2025-12-31, gamma0 VV < -18 dB = water), anchored on
sourced dam and falls points (Wikidata / OSM). Contains modified Copernicus Sentinel data 2024-2025.

| File | Status | Result |
|---|---|---|
| `pechiparai_max.geojson` | derived-draft | 12.0 km² largest water extent (+100 m buffer) |
| `chittar1_max.geojson` | derived-draft | 1.8 km² |
| `chittar2_max.geojson` | derived-draft | 3.1 km² |
| `falls_box.geojson` | draft | OSM weir to falls (217 m) + 200 m downstream, 120 m wide; replace with owner's GPS pins |
| `kodayar_reach.geojson` | not feasible with S1 | the river between the dam and the falls is not visible as water at 10 m |
| `catchment_zone_a/b.geojson` | placeholder | to derive from Copernicus DEM GLO-30 |

Previews: `previews/preview_reservoirs.png` (yellow Pechiparai, cyan Chittar I, magenta Chittar II, red = anchor points;
grey = share of passes that looked like water) and `previews/preview_river.png` (green = falls box draft).
Owner check pending: do the outlines match the reservoirs as you know them?
