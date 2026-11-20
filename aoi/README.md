# Areas of interest

Derived 18 Nov 2026 by `sat/derive_aois.py` (GitHub run 44281240797). No coordinates drawn by hand: the outlines come from
a Sentinel-1 water-frequency map (60 descending passes, 2024-01-01..2025-12-31, gamma0 VV < -18 dB = water), anchored on
sourced dam and falls points (Wikidata / OSM). Contains modified Copernicus Sentinel data 2024-2025.

| File | Status | Result |
|---|---|---|
| `pechiparai_max.geojson` | owner-checked 18 Nov 2026 | 12.0 km² largest water extent (+100 m buffer) |
| `chittar1_max.geojson` | owner-checked 18 Nov 2026 | 1.8 km² |
| `chittar2_max.geojson` | owner-checked 18 Nov 2026 | 3.1 km² |
| `falls_box.geojson` | owner-pinned 20 Nov 2026 | 100 m-wide strip along the owner's 4 GPS pins: upper falls, falls, lower falls, far lower falls (~970 m) |
| `kodayar_reach.geojson` | not feasible with S1 | the river between the dam and the falls is not visible as water at 10 m |
| `catchment_zone_a.geojson` | owner-checked 18 Nov 2026 | 170.8 km², land draining into Pechiparai (`sat/derive_zones.py`) |
| `catchment_zone_b.geojson` | owner-checked 18 Nov 2026 | 112.0 km², land draining to the Kodayar between Pechiparai and the falls (includes Chittar I/II) |

Previews: `previews/preview_reservoirs.png` (yellow Pechiparai, cyan Chittar I, magenta Chittar II, red = anchor points;
grey = share of passes that looked like water) and `previews/preview_river.png` (green = falls box draft).
Owner checked the three reservoir outlines on 18 Nov 2026: they match.

Zones A and B (18 Nov 2026): derived from the Copernicus DEM GLO-30 public tile N08 E077 (AWS Open Data, licence
COP-DEM-GLO-30-F "Full, Free & Open") by priority-flood routing at 30 m. Basin above the falls 282.8 km²; the result
does not change with the falls snap distance (60-200 m). Preview `previews/preview_zones.png` (yellow zone A, cyan
zone B, magenta Pechiparai lake, red = falls and Chittar dams). Credit: produced using Copernicus WorldDEM-30
© DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union
and ESA; all rights reserved.
Owner checked zones A and B in Google Earth (KML) on 18 Nov 2026: they look right.
Falls box (20 Nov 2026): rebuilt from the owner's GPS pins (upper 8.392391,77.258185; falls 8.391284,77.259428; lower 8.389084,77.260692; far lower 8.384710,77.259241). The falls pin is 6 m from the OSM falls point and the upper pin 44 m from the OSM weir.
