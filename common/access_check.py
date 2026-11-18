"""Check that the Copernicus Data Space and NASA Earthdata secrets work. Downloads no imagery and sends nothing to the site.

- CDSE: OAuth client-credentials token, then Catalog API search for Sentinel-1 GRD and Sentinel-2 L2A over a small
  box around Thirparappu Falls (~8.3913 N, 77.2594 E, spec §3) for the last 30 days. Prints dates and orbits, which
  also shows the real revisit over this area.
- Earthdata: find the IMERG daily collections in NASA CMR (public), then read the first 1 KB of one recent granule with
  the token (proves the token and the GES DISC app approval).
Never prints a secret. Exit code 1 if any check fails.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import requests

UA = {"User-Agent": "KumariLivePipeline/0.1"}
TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
CATALOG_URL = "https://sh.dataspace.copernicus.eu/api/v1/catalog/1.0.0/search"
CMR = "https://cmr.earthdata.nasa.gov/search"
# Access check only: a ~2 km box around the falls point from the spec. Not an AOI.
BBOX = [77.25, 8.38, 77.27, 8.40]


class NasaSession(requests.Session):
    """Keep the bearer token across redirects between NASA hosts only (requests drops it on any host change)."""

    def rebuild_auth(self, prepared, response):
        host = requests.utils.urlparse(prepared.url).hostname or ""
        if "Authorization" in prepared.headers and not (host.endswith(".nasa.gov")):
            del prepared.headers["Authorization"]


def env(name: str) -> str:
    return os.environ.get(name, "").replace("﻿", "").strip()


def check_cdse() -> bool:
    cid, secret = env("CDSE_CLIENT_ID"), env("CDSE_CLIENT_SECRET")
    if not cid or not secret:
        print("CDSE: secrets CDSE_CLIENT_ID / CDSE_CLIENT_SECRET are empty")
        return False
    r = requests.post(TOKEN_URL, data={"grant_type": "client_credentials", "client_id": cid, "client_secret": secret}, headers=UA, timeout=30)
    if r.status_code != 200:
        print(f"CDSE: token request failed, HTTP {r.status_code}: {r.text[:200]}")
        return False
    token = r.json()["access_token"]
    print("CDSE: token OK")
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)
    ok = True
    for coll in ("sentinel-1-grd", "sentinel-2-l2a"):
        body = {"collections": [coll], "bbox": BBOX, "datetime": f"{start:%Y-%m-%dT%H:%M:%SZ}/{end:%Y-%m-%dT%H:%M:%SZ}", "limit": 50}
        if coll == "sentinel-2-l2a":
            body["fields"] = {"include": ["properties.datetime", "properties.eo:cloud_cover"]}
        s = requests.post(CATALOG_URL, json=body, headers={**UA, "Authorization": f"Bearer {token}"}, timeout=60)
        if s.status_code != 200:
            print(f"CDSE: catalog {coll} failed, HTTP {s.status_code}: {s.text[:300]}")
            ok = False
            continue
        feats = s.json().get("features", [])
        print(f"CDSE: {coll}: {len(feats)} scenes in the last 30 days")
        for f in sorted(feats, key=lambda f: f["properties"].get("datetime", "")):
            p = f["properties"]
            extra = []
            for k in ("sat:orbit_state", "sat:relative_orbit", "sar:instrument_mode", "s1:polarization", "polarization", "eo:cloud_cover"):
                if k in p:
                    extra.append(f"{k}={p[k]}")
            print(f"   {p.get('datetime')}  {' '.join(extra)}")
        if feats:
            print(f"   (properties available: {', '.join(sorted(feats[0]['properties']))})")
    return ok


def check_earthdata() -> bool:
    token = env("EARTHDATA_TOKEN")
    if not token:
        print("Earthdata: secret EARTHDATA_TOKEN is empty")
        return False
    ok = True
    for short in ("GPM_3IMERGDE", "GPM_3IMERGDF"):
        c = requests.get(f"{CMR}/collections.json", params={"short_name": short, "provider": "GES_DISC"}, headers=UA, timeout=30)
        entries = c.json().get("feed", {}).get("entry", []) if c.ok else []
        if not entries:
            print(f"Earthdata: no CMR collection for {short} (HTTP {c.status_code})")
            ok = False
            continue
        versions = sorted(e.get("version_id", "?") for e in entries)
        latest = max(entries, key=lambda e: e.get("version_id", ""))
        print(f"Earthdata: {short} versions in CMR: {', '.join(versions)}; using {latest.get('version_id')}")
        g = requests.get(f"{CMR}/granules.json", params={"collection_concept_id": latest["id"], "sort_key": "-start_date", "page_size": 1}, headers=UA, timeout=30)
        gr = g.json().get("feed", {}).get("entry", []) if g.ok else []
        if not gr:
            print(f"Earthdata: {short}: no granules found")
            ok = False
            continue
        links = [l["href"] for l in gr[0].get("links", []) if l.get("href", "").startswith("https://") and l["href"].endswith((".nc4", ".HDF5", ".h5"))]
        print(f"Earthdata: {short}: latest granule {gr[0].get('time_start')} .. {gr[0].get('time_end')}")
        if not links:
            print(f"Earthdata: {short}: no https data link in the granule record")
            ok = False
            continue
        # Read 1 KB only. Redirects go through the Earthdata login; the bearer token must survive them.
        sess = NasaSession()
        sess.headers.update({**UA, "Authorization": f"Bearer {token}", "Range": "bytes=0-1023"})
        d = sess.get(links[0], timeout=60, allow_redirects=True, stream=True)
        chunk = next(d.iter_content(1024), b"")
        good = d.status_code in (200, 206) and (chunk[:4] == b"\x89HDF" or chunk[:3] == b"CDF")
        print(f"Earthdata: {short}: data access HTTP {d.status_code}, {'OK (HDF5 file header)' if good else 'NOT OK'} host={links[0].split('/')[2]}")
        if not good:
            if d.status_code in (401, 403):
                print("   Check: token not expired (60 days), and 'NASA GESDISC DATA ARCHIVE' approved under Applications > Authorized Apps.")
            ok = False
        d.close()
    return ok


if __name__ == "__main__":
    results = {"cdse": check_cdse(), "earthdata": check_earthdata()}
    print("RESULT:", ", ".join(f"{k}={'OK' if v else 'FAILED'}" for k, v in results.items()))
    sys.exit(0 if all(results.values()) else 1)
