"""Copernicus Data Space (Sentinel Hub APIs on CDSE): token, Catalog search, Process requests with rate-limit retry.
Never prints the token. Logs processing units per request."""
from __future__ import annotations

import os
import sys
import time

import requests

TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"
CATALOG_URL = "https://sh.dataspace.copernicus.eu/api/v1/catalog/1.0.0/search"
UA = {"User-Agent": "KumariLivePipeline/0.1"}


class Cdse:
    def __init__(self):
        self._tok, self._exp = None, 0.0
        self.pu = 0.0
        self.requests = 0

    def token(self) -> str:
        if self._tok and time.time() < self._exp - 60:
            return self._tok
        cid = os.environ.get("CDSE_CLIENT_ID", "").replace("﻿", "").strip()
        sec = os.environ.get("CDSE_CLIENT_SECRET", "").replace("﻿", "").strip()
        if not cid or not sec:
            sys.exit("CDSE_CLIENT_ID / CDSE_CLIENT_SECRET are not set")
        r = requests.post(TOKEN_URL, data={"grant_type": "client_credentials", "client_id": cid, "client_secret": sec}, headers=UA, timeout=30)
        r.raise_for_status()
        j = r.json()
        self._tok, self._exp = j["access_token"], time.time() + float(j.get("expires_in", 600))
        return self._tok

    def _post(self, url: str, body: dict, accept: str, timeout: int = 600) -> requests.Response:
        for attempt in range(6):
            r = requests.post(url, json=body, timeout=timeout,
                              headers={**UA, "Authorization": f"Bearer {self.token()}", "Accept": accept})
            self.requests += 1
            if r.status_code == 401 and attempt == 0:
                self._tok = None  # token expired mid-run
                continue
            if r.status_code not in (429, 502, 503, 504):
                return r
            # Free plan: processing units and requests per minute are limited. Wait as told (or longer each time).
            wait = int(float(r.headers.get("Retry-After", 0) or 0)) or 60 * (attempt + 1)
            wait = min(max(wait, 20), 300)
            print(f"   HTTP {r.status_code}, waiting {wait} s")
            time.sleep(wait)
        return r

    def process(self, body: dict, label: str = "") -> bytes:
        t0 = time.time()
        r = self._post(PROCESS_URL, body, "image/tiff")
        if r.status_code != 200:
            raise RuntimeError(f"{label}: Process API HTTP {r.status_code}: {r.text[:400]}")
        pu = float(r.headers.get("x-processingunits-spent", 0) or 0)
        self.pu += pu
        print(f"   {label}: {len(r.content)} bytes, {pu:.1f} PU, {time.time() - t0:.0f} s")
        return r.content

    def catalog(self, collection: str, bbox: list[float], start: str, end: str, crs: str | None = None) -> list[dict]:
        """All Catalog features (pages followed)."""
        out, nxt = [], None
        while True:
            body = {"collections": [collection], "bbox": bbox, "datetime": f"{start}/{end}", "limit": 100}
            if crs:
                body["bbox-crs"] = crs
            if nxt is not None:
                body["next"] = nxt
            r = self._post(CATALOG_URL, body, "application/geo+json", timeout=60)
            if r.status_code != 200:
                raise RuntimeError(f"Catalog HTTP {r.status_code}: {r.text[:400]}")
            j = r.json()
            out += j.get("features", [])
            nxt = j.get("context", {}).get("next")
            if nxt is None:
                return out
