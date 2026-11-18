"""Parse the TN-SMART daily reservoir bulletin (SATELLITE_WATER_SPEC.md §6.3).

Works on the bulletin's extracted text so it can be tested without real PDFs. Each dam row ends with five numbers:
total depth (ft), total capacity (Mcft), current depth (ft), current storage (Mcft), % storage. A row is accepted only
when its numbers agree with each other (depth within total, % close to storage / capacity) and its total depth matches
the dam's known full depth; anything else is reported as not parsed, never guessed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

# Name patterns (case-insensitive) and known full depth in ft (bulletin of 25-05-2025, WRD dam pages; spec §3).
DAMS: dict[str, tuple[str, float]] = {
    "pechiparai": (r"pech\w*", 48.0),
    "perunchani": (r"perunchan\w*", 77.0),
    "chittar_1": (r"chittar\s*-?\s*(?:i|1)(?![i\d])", 18.0),
    "chittar_2": (r"chittar\s*-?\s*(?:ii|2)(?![i\d])", 18.0),
}

_NUM = r"(\d+(?:\.\d+)?)"
_FIVE = rf"{_NUM}\s+{_NUM}\s+{_NUM}\s+{_NUM}\s+{_NUM}\s*%?\s*$"
_ASON = re.compile(r"as\s+on\s*\(?\s*(\d{2})-(\d{2})-(\d{4})", re.I | re.S)
_ASON_LOOSE = re.compile(r"as\s+on.{0,200}?(\d{2})-(\d{2})-(\d{4})", re.I | re.S)


@dataclass(frozen=True)
class DamRow:
    place: str
    total_depth_ft: float
    capacity_mcft: float
    depth_ft: float
    storage_mcft: float
    pct: float


@dataclass(frozen=True)
class Bulletin:
    as_on: date | None
    rows: dict[str, DamRow]
    problems: dict[str, str]


def bulletin_date(text: str) -> date | None:
    m = _ASON.search(text) or _ASON_LOOSE.search(text)
    if not m:
        return None
    d, mo, y = (int(x) for x in m.groups())
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _check(place: str, nums: list[float], full_ft: float) -> str | None:
    total, cap, depth, storage, pct = nums
    if abs(total - full_ft) > 0.01:
        return f"total depth {total} is not the known {full_ft} ft"
    if not 0 <= depth <= total + 0.5:
        return f"current depth {depth} outside 0..{total}"
    if not 0 <= storage <= cap * 1.05:
        return f"storage {storage} outside 0..{cap}"
    if cap > 0 and abs(storage / cap * 100 - pct) > 1.0:
        return f"percent {pct} does not match storage / capacity"
    return None


def parse_text(text: str) -> Bulletin:
    rows: dict[str, DamRow] = {}
    problems: dict[str, str] = {}
    lines = text.splitlines()
    for place, (name, full_ft) in DAMS.items():
        pat = re.compile(rf"\b{name}\b.*?{_FIVE}", re.I)
        hits = []
        for i, line in enumerate(lines):
            if not re.search(rf"\b{name}", line, re.I):
                continue
            # The name may wrap ("Pechippar / ai"): try the line alone, then joined with the next one.
            for cand in (line, line + " " + (lines[i + 1] if i + 1 < len(lines) else "")):
                m = pat.search(cand)
                if m:
                    hits.append([float(x) for x in m.groups()])
                    break
        if not hits:
            problems[place] = "row not found"
            continue
        if len(hits) > 1 and any(h != hits[0] for h in hits):
            problems[place] = f"{len(hits)} different rows match"
            continue
        why = _check(place, hits[0], full_ft)
        if why:
            problems[place] = why
            continue
        rows[place] = DamRow(place, *hits[0])
    return Bulletin(bulletin_date(text), rows, problems)


def pdf_text(path_or_bytes) -> str:
    """Text of every page, in reading order, via pdfplumber (pinned in requirements-water.txt)."""
    import io
    import pdfplumber

    src = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, (bytes, bytearray)) else path_or_bytes
    with pdfplumber.open(src) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)
