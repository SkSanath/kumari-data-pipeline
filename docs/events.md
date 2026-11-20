# Events for the backtest (`docs/events.csv`)

Ground truth for SATELLITE_WATER_SPEC.md §8. Public-safe: dates, types, links and short notes in our own words; no
TN-SMART numbers. Dam levels and cusecs in the notes are quoted from the linked news reports.

Columns: `date` (YYYY-MM-DD, IST), `type`, `level_observed` (owner observations only), `source_url`, `notes`.

| type | meaning |
|---|---|
| `ban` | bathing or visiting banned at Thirparappu falls (one row per reported day or episode start) |
| `ban_lifted` | the ban was lifted (added 20 Nov 2026 so the backtest knows how long a ban lasted) |
| `flood` | the falls or the Kodayar reported in flood with no ban stated |
| `surplus_release` | surplus or flood release from Pechiparai (and/or Perunchani, Chittar) |
| `closure` | falls closed for a reason other than water (maintenance, festival, VIP) |
| `irrigation_open` / `irrigation_close` | irrigation release started or stopped (drives the season flag) |
| `owner_obs` | the owner's own observation, with `level_observed` = Dry / Low / Moderate / Good / High / Danger |

Rules: every row needs a dated source (news link, Collector notice, or the owner's dated photo). A date worked out from
the article ("on Saturday", "day 6") says so in the notes. Planned dates (a G.O.'s end date) say PLANNED until confirmed.
Rows from the spec without a link are marked "check before use".

How the owner adds an observation: one line, e.g.
`2026-11-22,owner_obs,Good,,"Owner visit 4 pm, photo IMG_1234 (EXIF date); water over the whole rock face"`

First search: 20 Nov 2026 (English and Tamil news: DT Next, Daily Thanthi, Thanthi TV, Maalai Malar, Tamil Janam,
Dinakaran, IANS). Not found yet: the 2023 and 2024 Kodayar irrigation openings, the 2024 and 2026 closures, and the
spec's 2024-05-19 release. Some Dinakaran/Dinamani pages (via Magzter) refused automated reading (HTTP 403).
