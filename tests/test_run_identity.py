"""A run is known by its flight, and dated by when it was flown.

Runs used to be named everywhere by their folder (console_20261105_191505) and
dated by when the ANALYSIS ran - a year after the flight in the real data. A
crew asks which flight a run is and when it was flown; neither answer was on
screen. These pin the title (operator's, or built from capture time), the
flight's own date, the rename endpoint, and the same date on the PDF cover.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from src.wildfire.config import Settings
from src.wildfire.console import data
from src.wildfire.gps import capture_window, flown_labels


def _run(tmp_path, stamps, name="console_20261105_191505"):
    run = tmp_path / name
    run.mkdir(parents=True)
    images = [{"path": f"/s/{i}.JPG", "name": f"{i}.JPG", "width": 9, "height": 9,
               "timestamp": ts, "detections": [], "flagged": False}
              for i, ts in enumerate(stamps)]
    (run / "batch.json").write_text(json.dumps({"batch_info": {}, "stats": {}, "images": images}))
    return run, Settings(output_dir=str(tmp_path), models_dir=str(tmp_path), model_sources=[])


# ---------------------------------------------------------------- the dates


def test_a_flight_window_from_exif_capture_times():
    w = capture_window(["2025:05:02 14:55:10", "2025:05:02 14:52:30", None, "garbage"])
    assert w == (datetime(2025, 5, 2, 14, 52, 30), datetime(2025, 5, 2, 14, 55, 10))
    assert flown_labels(w) == ("May 2, 2025", "14:52–14:55")


def test_a_single_photo_has_one_time_and_no_capture_time_has_none():
    w = capture_window(["2025:05:02 14:59:00"])
    assert flown_labels(w) == ("May 2, 2025", "14:59")
    assert capture_window([None, ""]) is None
    assert flown_labels(None) == (None, None)


def test_a_flight_across_days_or_years_drops_the_times():
    across_days = capture_window(["2025:05:02 23:50:00", "2025:05:03 00:10:00"])
    assert flown_labels(across_days) == ("May 2 – May 3, 2025", None)
    across_years = capture_window(["2025:12:31 23:50:00", "2026:01:01 00:10:00"])
    assert flown_labels(across_years) == ("Dec 31, 2025 – Jan 1, 2026", None)


# ---------------------------------------------------------------- the title


def test_the_title_is_built_from_capture_time_not_the_folder(tmp_path):
    run, s = _run(tmp_path, ["2025:05:02 14:52:30", "2025:05:02 14:55:10"])
    summary = data.scan_summary(run, s)
    assert summary["title"] == "May 2, 2025 · 14:52–14:55"
    assert summary["title_custom"] is False
    assert summary["flown_date"] == "May 2, 2025" and summary["flown_time"] == "14:52–14:55"
    assert summary["analysed"].startswith("Nov 5, 2026"), "the analysis time is kept, as secondary"
    assert summary["id"] == "console_20261105_191505", "the folder id is still there for anyone who needs it"


def test_a_run_without_capture_times_says_so_rather_than_inventing_one(tmp_path):
    run, s = _run(tmp_path, [None])
    summary = data.scan_summary(run, s)
    assert summary["title"].startswith("Analysed Nov 5, 2026")
    assert summary["flown_date"] is None


def test_a_renamed_run_keeps_its_name_and_a_cleared_name_falls_back(tmp_path):
    run, s = _run(tmp_path, ["2025:05:02 14:52:30"])
    assert data.write_run_title(run, "  Canmore   Creek \t north  ") == "Canmore Creek north"
    assert data.scan_summary(run, s)["title"] == "Canmore Creek north"
    assert data.scan_summary(run, s)["title_custom"] is True

    assert data.write_run_title(run, "") == ""
    assert not (run / "run.json").exists(), "an empty name leaves no file behind"
    assert data.scan_summary(run, s)["title"].startswith("May 2, 2025")


def test_a_title_is_capped(tmp_path):
    run, _ = _run(tmp_path, [])
    assert len(data.write_run_title(run, "x" * 500)) == data.TITLE_MAX


def test_the_rename_endpoint(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from src.wildfire.console.server import create_app

    run, s = _run(tmp_path, ["2025:05:02 14:52:30"])
    client = TestClient(create_app(s))
    r = client.post(f"/api/scans/{run.name}/title", json={"title": "North block"})
    assert r.status_code == 200 and r.json() == {"title": "North block", "title_custom": True}
    assert client.get(f"/api/scans/{run.name}").json()["title"] == "North block"
    assert client.get("/api/map-data").json()["run_titles"][run.name] == "North block"

    reset = client.post(f"/api/scans/{run.name}/title", json={"title": ""}).json()
    assert reset["title_custom"] is False and reset["title"].startswith("May 2, 2025")
    assert client.post("/api/scans/nope/title", json={"title": "x"}).status_code == 404


def test_the_report_cover_says_when_it_was_flown(tmp_path):
    pytest.importorskip("reportlab")
    from pypdf import PdfReader

    from src.wildfire.report import build_report
    from src.wildfire.types import BatchResult, ImageResult

    batch = BatchResult(images=[
        ImageResult(path="a.JPG", name="a.JPG", width=9, height=9, timestamp="2025:05:02 14:52:30"),
        ImageResult(path="b.JPG", name="b.JPG", width=9, height=9, timestamp="2025:05:02 14:55:10"),
    ], stats={"images_processed": 2}, batch_info={"batch_label": "x", "generated_at": "2026-11-05 19:15:00"})
    text = PdfReader(str(build_report(batch, tmp_path / "r.pdf"))).pages[0].extract_text()
    assert "Flown" in text and "May 2, 2025, 14:52-14:55" in text


# ---------------------------------------------------------------- the window


@pytest.mark.parametrize("screen,expect", [
    ((1366, 768), (1256, 691, (1024, 640))),    # a typical field laptop: fits now
    ((1920, 1080), (1440, 920, (1024, 640))),   # roomy: the preferred size
    ((1024, 600), (1024, 600, (1024, 600))),    # smaller than the floor: the screen wins
])
def test_the_window_fits_the_screen_it_opens_on(monkeypatch, screen, expect):
    """It used to open at a fixed 1440x920, larger than a 1366x768 panel."""
    import sys
    import types

    from src.wildfire.console import desktop

    fake = types.SimpleNamespace(screens=[types.SimpleNamespace(width=screen[0], height=screen[1])])
    monkeypatch.setitem(sys.modules, "webview", fake)
    assert desktop._window_geometry() == expect


def test_the_window_falls_back_when_the_screen_is_unknown(monkeypatch):
    import sys
    import types

    from src.wildfire.console import desktop

    monkeypatch.setitem(sys.modules, "webview", types.SimpleNamespace(screens=[]))
    assert desktop._window_geometry() == (1440, 920, (1024, 640))
