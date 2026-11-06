"""What the scan page tells the operator about a box, and about the run itself.

Three things a reviewer used to be misled by:
  - box sizes in pixels, which say nothing on the ground; the frame's ground
    resolution is now stored with each image so sizes can be read in metres;
  - severity on runs made by an older detector, graded on thresholds calibrated
    for the new one (gen 1 found ~70 "dead trees" per frame, gen 2 ~20);
  - no way to bring such a run up to date except starting over from the SD card.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src.wildfire.config import DETECTOR_GENERATION, Settings
from src.wildfire.console import data
from src.wildfire.types import ImageResult

PAGES = Path(__file__).resolve().parents[1] / "src" / "wildfire" / "console" / "pages"


def _run(tmp_path, batch_info, paths=("/nowhere/a.JPG",), name="console_20261105_191505"):
    run = tmp_path / name
    run.mkdir(parents=True)
    images = [{"path": str(p), "name": Path(p).name, "width": 9, "height": 9,
               "detections": [], "flagged": False} for p in paths]
    (run / "batch.json").write_text(json.dumps({"batch_info": batch_info, "stats": {}, "images": images}))
    return run, Settings(output_dir=str(tmp_path), models_dir=str(tmp_path), model_sources=[])


# ---------------------------------------------------------------- ground scale


def test_ground_resolution_survives_batch_json():
    im = ImageResult(path="/s/a.JPG", name="a.JPG", width=10, height=10, detections=[], gsd_cm=1.42)
    assert ImageResult.from_dict(im.to_dict()).gsd_cm == 1.42
    # runs written before the field existed still load
    legacy = im.to_dict()
    legacy.pop("gsd_cm")
    assert ImageResult.from_dict(legacy).gsd_cm is None


def test_stored_ground_resolution_is_used_without_reading_the_photo():
    assert data._image_gsd({"gsd_cm": 1.4, "path": "/does/not/exist.JPG"}) == 1.4
    assert data._image_gsd({"path": "/does/not/exist.JPG"}) is None


# ---------------------------------------------------------------- freshness


def test_a_run_without_a_generation_is_from_the_older_detector(tmp_path):
    run, s = _run(tmp_path, {})
    assert data.scan_summary(run, s)["detector_stale"] is True


def test_a_run_from_before_the_field_is_dated_by_what_it_found():
    # fallen trees exist only from generation 2
    assert data.detector_generation({"stats": {"detections_by_type": {"Fallen Tree": 3}}}) == 2
    assert data.detector_generation({"stats": {"detections_by_type": {"Dead Tree": 70}}}) == 1
    assert data.detector_generation({"batch_info": {"detector_generation": 7}}) == 7


def test_a_run_from_this_detector_is_not_stale(tmp_path):
    run, s = _run(tmp_path, {"detector_generation": DETECTOR_GENERATION})
    assert data.scan_summary(run, s)["detector_stale"] is False


def test_the_dashboard_counts_stale_scans(tmp_path):
    _run(tmp_path, {}, name="console_20260101_000000")
    _, s = _run(tmp_path, {"detector_generation": DETECTOR_GENERATION})
    assert data.dashboard_summary(s)["stale_scans"] == 1


def test_the_pipeline_records_its_generation():
    src = (Path(__file__).resolve().parents[1] / "src" / "wildfire" / "pipeline.py").read_text()
    assert '"detector_generation": DETECTOR_GENERATION' in src


def test_redetect_needs_the_original_photos(tmp_path):
    run, s = _run(tmp_path, {}, paths=("/nowhere/a.JPG",))
    assert data.scan_summary(run, s)["can_redetect"] is False
    photo = tmp_path / "a.JPG"
    photo.write_bytes(b"x")
    run2, s = _run(tmp_path, {}, paths=(photo,), name="console_20260202_000000")
    assert data.scan_summary(run2, s)["can_redetect"] is True


# ---------------------------------------------------------------- the endpoint


def _client(s):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from src.wildfire.console.server import create_app

    app = create_app(s)
    started = []

    class Job:
        def to_dict(self):
            return {"id": "job_x"}

    class Jobs:
        def start_detection(self, paths, settings):
            started.append(list(paths))
            return Job()

    app.state.jobs = Jobs()
    return TestClient(app), started


def test_redetect_starts_a_new_job_over_the_photos_still_on_disk(tmp_path):
    photo = tmp_path / "a.JPG"
    photo.write_bytes(b"x")
    run, s = _run(tmp_path, {}, paths=(photo, "/nowhere/b.JPG"))
    client, started = _client(s)
    r = client.post(f"/api/scans/{run.name}/redetect")
    assert r.status_code == 202
    assert r.json()["images"] == 1 and r.json()["missing"] == 1
    assert started == [[photo]]
    # the old run is left as it was
    assert json.loads((run / "batch.json").read_text())["batch_info"] == {}


def test_redetect_refuses_when_nothing_can_be_found(tmp_path):
    run, s = _run(tmp_path, {})
    client, started = _client(s)
    assert client.post(f"/api/scans/{run.name}/redetect").status_code == 400
    assert client.post("/api/scans/console_19990101_000000/redetect").status_code == 404
    assert started == []


# ---------------------------------------------------------------- the pages


def test_every_run_list_shows_the_stale_chip():
    for page in ("scans.html", "review.html", "dashboard.html", "detail.html"):
        assert "staleChip(" in (PAGES / page).read_text(encoding="utf-8"), page


def test_the_scan_view_draws_live_boxes_from_the_verdict():
    js = (PAGES / "detail.html").read_text(encoding="utf-8")
    # one source of boxes for overlay and list: confirmed if reviewed, else proposals
    assert re.search(r"function sourceBoxes\(im\)", js)
    assert "shownBoxes(" in js and "overlaySvg(" in js
    # sizes read in metres where the frame's scale is known
    assert re.search(r"function boxSize\(", js) and "gsd_cm" in js


def test_settings_keep_engineering_knobs_folded_and_guard_unsaved_edits():
    html = (PAGES / "settings.html").read_text(encoding="utf-8")
    assert re.search(r"<details class=\"card advanced\" id=\"advanced\"", html)
    assert 'id="save-bar"' in html
    assert "WF_LEAVE_GUARD" in html
