"""TN-SMART parser tests.

The fixtures copy the bulletin's LAYOUT (as pdfplumber extracts it, checked against real bulletins of 2025-2026), but
every number in them is MADE UP: the bulletins' licence is not stated, so their numbers never enter this public repo.
`test_real_bulletins` runs over real PDFs only where a local, git-ignored `tnsmart_cache/` holds some.
"""
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))
sys.path.insert(0, str(ROOT / "tnsmart"))
import fetch_parse  # noqa: E402
from parse import parse_text  # noqa: E402

# Made-up values. Layout as extracted: dam rows end with 5 numbers; district wraps ("Kanniyaku / mari");
# the date header wraps; the last page repeats the header.
FAKE = """BASIN WISE SURFACE WATER STORAGE POSITION OF TAMILNADU (as on
07-03-2031)
Sl. No. District Name of Total depth Total Current Current Percentage
the Dam / in feet storage depth in Storage as of Storage
84 Kodaiyar Kanniyaku Pechipparai 48.00 4350.00 30.00 2000 45.98
mari
85 Kodaiyar Kanniyaku Perunchani 77.00 2890.00 50.00 1000 34.60
mari
BASIN WISE SURFACE WATER STORAGE POSITION OF TAMILNADU (as on
07-03-2031)
86 Kodaiyar Kanniyaku Chittar I 18.00 393.00 10.00 200.00 50.89
mari
87 Kodaiyar Kanniyaku Chittar II 18.00 600.00 11.00 300.00 50.0
mari
Total 8382.39 3500.00 41.75 %
"""


def test_reads_all_four_dams_and_the_date():
    b = parse_text(FAKE)
    assert b.as_on == date(2031, 3, 7)
    assert b.problems == {}
    assert b.rows["pechiparai"].depth_ft == 30.0
    assert b.rows["pechiparai"].storage_mcft == 2000
    assert b.rows["perunchani"].pct == 34.60


def test_chittar_i_and_ii_are_not_confused():
    b = parse_text(FAKE)
    assert b.rows["chittar_1"].capacity_mcft == 393.0
    assert b.rows["chittar_2"].capacity_mcft == 600.0


def test_four_decimal_percent_2026_style():
    b = parse_text(FAKE.replace("45.98", "45.9770"))
    assert b.rows["pechiparai"].pct == pytest.approx(45.977)


def test_spelling_pechiparai_and_wrapped_name():
    assert "pechiparai" in parse_text(FAKE.replace("Pechipparai", "PECHIPARAI")).rows
    wrapped = FAKE.replace("Kanniyaku Pechipparai 48.00 4350.00 30.00 2000 45.98", "Kanniyaku Pechippar\nai 48.00 4350.00 30.00 2000 45.98")
    assert parse_text(wrapped).rows["pechiparai"].depth_ft == 30.0


def test_inconsistent_rows_are_refused_not_guessed():
    bad_pct = parse_text(FAKE.replace("2000 45.98", "2000 80.00"))
    assert "pechiparai" not in bad_pct.rows and "percent" in bad_pct.problems["pechiparai"]
    bad_total = parse_text(FAKE.replace("Pechipparai 48.00", "Pechipparai 46.00"))
    assert "total depth" in bad_total.problems["pechiparai"]
    too_deep = parse_text(FAKE.replace("4350.00 30.00 2000", "4350.00 60.00 2000"))
    assert "pechiparai" not in too_deep.rows


def test_missing_row_is_reported():
    b = parse_text(FAKE.replace("Chittar II", "Somewhere"))
    assert b.problems["chittar_2"] == "row not found"
    assert "chittar_1" in b.rows


def test_404_becomes_missing_rows(monkeypatch):
    rows, _ = fetch_parse.rows_for(date(2031, 3, 7), "https://example.test/x.pdf", None, datetime(2031, 3, 8, tzinfo=timezone.utc))
    assert len(rows) == 4 * 3
    assert all(r["value"] is None and r["quality"] == "missing" and r["visibility"] == "private" for r in rows)
    assert rows[0]["meta"]["why"] == "no bulletin (404)"


def test_ok_rows_are_private_and_dated(monkeypatch):
    monkeypatch.setattr(fetch_parse, "pdf_text", lambda _b: FAKE)
    rows, problems = fetch_parse.rows_for(date(2031, 3, 7), "https://example.test/x.pdf", b"%PDF-", datetime(2031, 3, 8, tzinfo=timezone.utc))
    assert problems == {}
    depth = next(r for r in rows if r["place"] == "pechiparai" and r["signal"] == "dam_depth_ft")
    assert depth["value"] == 30.0 and depth["visibility"] == "private"
    assert depth["observed_at"] == "2031-03-06T18:30:00Z"  # 00:00 IST on the bulletin date
    assert depth["meta"]["date_only"] is True


def test_bulletin_for_another_date_is_stored_as_missing(monkeypatch):
    monkeypatch.setattr(fetch_parse, "pdf_text", lambda _b: FAKE)
    rows, problems = fetch_parse.rows_for(date(2031, 3, 8), "https://example.test/x.pdf", b"%PDF-", datetime(2031, 3, 9, tzinfo=timezone.utc))
    assert "*" in problems
    assert all(r["value"] is None for r in rows)


CACHE = sorted((ROOT / "tnsmart_cache").glob("*.pdf"))


@pytest.mark.skipif(not CACHE, reason="no local TN-SMART PDFs (private, never committed)")
@pytest.mark.parametrize("pdf", CACHE, ids=[p.stem for p in CACHE])
def test_real_bulletins(pdf):
    from parse import pdf_text

    b = parse_text(pdf_text(str(pdf)))
    assert b.problems == {}, b.problems
    assert set(b.rows) == {"pechiparai", "perunchani", "chittar_1", "chittar_2"}
    assert b.as_on is not None and b.as_on.isoformat() == pdf.stem
