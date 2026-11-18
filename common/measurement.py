"""Build and send measurements to the site's /api/ingest/signals (SATELLITE_WATER_SPEC.md §2.2, §9.1).

Every value carries source, source_url, observed_at and fetched_at. Missing is sent as value None with
quality "missing", never filled in.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

QUALITIES = {"ok", "suspect", "poor", "missing"}
VISIBILITIES = {"public", "private"}
_NAME = re.compile(r"^[a-z0-9_]{2,64}$")
_PLACE = re.compile(r"^[a-z0-9_-]{2,64}$")


def utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("datetime must carry a time zone")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def measurement(*, signal: str, place: str, value: float | None, unit: str, observed_at: datetime,
                source: str, source_url: str, quality: str, visibility: str,
                product_id: str | None = None, meta: dict | None = None,
                fetched_at: datetime | None = None) -> dict:
    """One measurement, checked the same way the site checks it, so bad rows fail here, not at the site."""
    if not _NAME.match(signal):
        raise ValueError(f"bad signal name: {signal!r}")
    if not _PLACE.match(place):
        raise ValueError(f"bad place: {place!r}")
    if quality not in QUALITIES:
        raise ValueError(f"bad quality: {quality!r}")
    if visibility not in VISIBILITIES:
        raise ValueError(f"bad visibility: {visibility!r}")
    if (value is None) != (quality == "missing"):
        raise ValueError("value must be None exactly when quality is 'missing'")
    if not source or not source_url.startswith(("https://", "http://")):
        raise ValueError("source and source_url are required")
    fetched = fetched_at or datetime.now(timezone.utc)
    if observed_at.tzinfo is None or fetched.tzinfo is None:
        raise ValueError("observed_at and fetched_at must carry a time zone")
    if observed_at > fetched:
        raise ValueError("observed_at is after fetched_at")
    out = {
        "signal": signal, "place": place, "value": None if value is None else float(value), "unit": unit,
        "observed_at": utc_iso(observed_at), "fetched_at": utc_iso(fetched),
        "source": source, "source_url": source_url, "quality": quality, "visibility": visibility,
    }
    if product_id:
        out["product_id"] = product_id
    if meta:
        out["meta"] = meta
    return out


def send(rows: list[dict], batch: int = 500) -> int:
    """POST rows in batches to /api/ingest/signals. Returns the number sent."""
    from post_ingest import post  # same folder; stdlib only

    for i in range(0, len(rows), batch):
        post("signals", rows[i:i + batch])
    return len(rows)
