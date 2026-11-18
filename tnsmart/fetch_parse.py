"""Fetch TN-SMART daily reservoir bulletins, parse four Kanniyakumari dams, POST them PRIVATE to the site.

Licence not stated (SATELLITE_WATER_SPEC.md §2.3): every row is sent with visibility "private", PDFs are never written
to the repo, and numbers are never printed to the (public) Actions log; only counts and problems are.

  python tnsmart/fetch_parse.py                      # today and yesterday (IST)
  python tnsmart/fetch_parse.py --from 2025-01-01 --to 2025-12-31
  python tnsmart/fetch_parse.py --date 2025-08-15 --dry-run

Archive coverage seen on 18 Nov 2026: from January 2025 only (no 2023-2024 files), with a gap around Apr to mid-Jun 2025.
A 404 is stored as missing, never filled in.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))
sys.path.insert(0, str(ROOT / "tnsmart"))
from measurement import measurement, send  # noqa: E402
from parse import DAMS, parse_text, pdf_text  # noqa: E402

BASE = "https://beta-tnsmart.rimes.int/DATA/Rainfall/daily_reservoir"
SOURCE = "TN-SMART daily reservoir bulletin"
IST = timezone(timedelta(hours=5, minutes=30))
UA = "KumariLivePipeline/0.1"
SIGNALS = [("dam_depth_ft", "ft", "depth_ft"), ("dam_storage_mcft", "Mcft", "storage_mcft"), ("dam_storage_pct", "%", "pct")]


def public_reason(msg: str) -> str:
    """A problem message safe for the PUBLIC Actions log: every number from the bulletin replaced by '#'.
    (Dates in 'bulletin says as on ...' are replaced too; the full message goes to the site as private meta.)"""
    return re.sub(r"\d[\d.\-]*", "#", msg)


def urls(d: date) -> list[str]:
    return [f"{BASE}/reservoir_{d:%d-%m-%Y}.pdf", f"{BASE}/reservoir_{d:%Y-%m-%d}.pdf"]


def fetch(d: date) -> tuple[str, bytes | None]:
    """(url, pdf bytes) for the first pattern that exists, else (first url, None)."""
    for u in urls(d):
        req = urllib.request.Request(u, headers={"User-Agent": UA})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    body = r.read()
                    if body[:5] == b"%PDF-":
                        return u, body
                    break
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    break
                time.sleep(2 ** attempt)
            except urllib.error.URLError:
                time.sleep(2 ** attempt)
    return urls(d)[0], None


def rows_for(d: date, url: str, pdf: bytes | None, fetched: datetime) -> tuple[list[dict], dict[str, str]]:
    # The bulletin gives a date, not a time: observed_at is that date at 00:00 IST, flagged date_only.
    observed = datetime(d.year, d.month, d.day, tzinfo=IST)
    meta = {"date_only": True, "bulletin_date": d.isoformat()}
    common = dict(observed_at=observed, fetched_at=fetched, source=SOURCE, source_url=url, visibility="private")
    out: list[dict] = []
    problems: dict[str, str] = {}
    b = parse_text(pdf_text(pdf)) if pdf else None
    if b is not None and b.as_on != d:
        problems["*"] = f"bulletin says as on {b.as_on}, expected {d}: stored as missing"
        b = None
    for place in DAMS:
        row = b.rows.get(place) if b else None
        if b and not row:
            problems[place] = b.problems.get(place, "not parsed")
        for sig, unit, attr in SIGNALS:
            if row:
                out.append(measurement(signal=sig, place=place, value=getattr(row, attr), unit=unit, quality="ok", meta=meta, **common))
            else:
                why = "no bulletin (404)" if pdf is None else problems.get(place) or problems.get("*", "not parsed")
                out.append(measurement(signal=sig, place=place, value=None, unit=unit, quality="missing", meta={**meta, "why": why}, **common))
    return out, problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--from", dest="start")
    ap.add_argument("--to", dest="end")
    ap.add_argument("--dry-run", action="store_true", help="parse only, send nothing")
    a = ap.parse_args()
    today = datetime.now(IST).date()
    if a.date:
        days = [date.fromisoformat(a.date)]
    elif a.start:
        s, e = date.fromisoformat(a.start), date.fromisoformat(a.end) if a.end else today
        days = [s + timedelta(n) for n in range((e - s).days + 1)]
    else:
        days = [today - timedelta(1), today]

    t0, found, missing, nbytes, rows = time.time(), 0, 0, 0, []
    for d in days:
        url, pdf = fetch(d)
        nbytes += len(pdf or b"")
        r, problems = rows_for(d, url, pdf, datetime.now(timezone.utc))
        rows += r
        found += pdf is not None
        missing += pdf is None
        for k, v in problems.items():
            print(f"{d} {k}: {public_reason(v)}")
        time.sleep(0.5)  # be gentle with the publisher
    print(f"days={len(days)} bulletins={found} missing={missing} rows={len(rows)} bytes={nbytes} secs={time.time() - t0:.0f}")
    if not a.dry_run and rows:
        send(rows)
        print("sent (private)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
