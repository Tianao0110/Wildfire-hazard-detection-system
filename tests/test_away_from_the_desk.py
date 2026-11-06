"""A flight takes ~20 minutes to detect, and the operator walks away.

These pin what brings them back: a time-left estimate that ignores model
loading, a hook that fires when a job ends, the jobs list every page header
polls, the desktop window's notification and progress label, and the window
reopening where it was left - but never on a screen that is gone.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src.wildfire.config import Settings
from src.wildfire.console import desktop
from src.wildfire.console.jobs import Job, JobManager

CONSOLE = Path(__file__).resolve().parents[1] / "src" / "wildfire" / "console"


# ---------------------------------------------------------------- time left


def test_no_estimate_until_two_images_give_a_pace():
    job = Job(id="r", state="running", total=100)
    assert job.eta_seconds(now=0) is None
    job.record_progress(1, now=30.0)          # model loading came before this
    assert job.eta_seconds(now=30.0) is None
    job.record_progress(11, now=130.0)        # 10 images in 100 s
    assert job.eta_seconds(now=130.0) == 890  # 89 left at 10 s each


def test_a_finished_job_has_no_estimate():
    job = Job(id="r", state="running", total=10)
    job.record_progress(1, now=0.0)
    job.record_progress(5, now=40.0)
    job.state = "done"
    assert job.eta_seconds(now=40.0) is None
    assert "eta_s" in job.to_dict() and "finished" in job.to_dict()


def test_finish_callbacks_run_and_a_failing_one_does_not_stop_the_rest():
    jobs = JobManager()
    seen = []
    jobs.on_finish.append(lambda j: 1 / 0)
    jobs.on_finish.append(lambda j: seen.append(j.id))
    job = Job(id="r", state="done")
    jobs._finished(job)
    assert seen == ["r"] and job.finished


def test_every_page_can_list_the_jobs(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from src.wildfire.console.server import create_app

    app = create_app(Settings(output_dir=str(tmp_path), models_dir=str(tmp_path), model_sources=[]))
    app.state.jobs._jobs["console_20260101_000000"] = Job(id="console_20260101_000000",
                                                           state="running", total=4, done=1)
    body = TestClient(app).get("/api/jobs").json()
    assert body["jobs"][0]["id"] == "console_20260101_000000"
    assert body["jobs"][0]["state"] == "running"


# ---------------------------------------------------------------- the desktop


def test_progress_label_spans_all_active_jobs():
    jobs = JobManager()
    assert desktop._progress_label(jobs) is None
    jobs._jobs["a"] = Job(id="a", state="running", total=10, done=5)
    jobs._jobs["b"] = Job(id="b", state="queued", total=10, done=0)
    jobs._jobs["c"] = Job(id="c", state="done", total=10, done=10)
    assert desktop._progress_label(jobs) == "25%"


def test_the_notification_names_the_flight_and_what_was_found(tmp_path):
    run = tmp_path / "console_20261105_191505"
    run.mkdir()
    (run / "batch.json").write_text(json.dumps({
        "batch_info": {}, "stats": {"detections_by_type": {"Dead Tree": 3}},
        "images": [{"path": "/s/a.JPG", "name": "a.JPG", "width": 9, "height": 9,
                    "timestamp": "2025:05:02 14:52:30", "detections": []}]}))
    s = Settings(output_dir=str(tmp_path), models_dir=str(tmp_path), model_sources=[])
    title, body = desktop._finish_message(Job(id=run.name, state="done", total=1), s)
    assert title == "Detection finished"
    assert "May 2, 2025" in body and "1 image," in body and "3 Dead Tree" in body
    title, body = desktop._finish_message(Job(id="x", state="error", error="out of memory"), s)
    assert title == "Detection failed" and "out of memory" in body


def test_notification_text_is_passed_as_data_not_script(monkeypatch):
    calls = []
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda *a, **k: calls.append((a, k)))
    nasty = '" & do shell script "rm -rf ~" & "'
    for platform in ("darwin", "win32"):
        monkeypatch.setattr(desktop.sys, "platform", platform)
        desktop._notify("Detection finished", nasty)
    mac, win = calls
    assert mac[0][0][-1] == nasty and all(nasty not in part for part in mac[0][0][:-1])
    assert nasty not in " ".join(win[0][0]) and win[1]["env"]["WF_NOTE_BODY"] == nasty


# ---------------------------------------------------------------- window place

SCREEN = [(0, 0, 1440, 900)]


def test_a_saved_window_comes_back_where_it_was():
    state = {"x": 100, "y": 80, "width": 1200, "height": 760, "maximized": False}
    assert desktop._restored_geometry(state, SCREEN, (1024, 640)) == state


def test_a_window_on_a_screen_that_is_gone_is_not_restored():
    # left on an external monitor to the right, laptop now undocked
    state = {"x": 1600, "y": 100, "width": 1200, "height": 760}
    assert desktop._restored_geometry(state, SCREEN, (1024, 640)) is None
    # title bar above the top of the screen: could not be grabbed
    assert desktop._restored_geometry({**state, "x": 0, "y": -300}, SCREEN, (1024, 640)) is None


def test_a_restored_window_is_clamped_to_its_screen_and_the_minimum():
    big = desktop._restored_geometry({"x": 0, "y": 0, "width": 3000, "height": 2000}, SCREEN, (1024, 640))
    assert (big["width"], big["height"]) == (1440, 900)
    small = desktop._restored_geometry({"x": 0, "y": 0, "width": 300, "height": 200}, SCREEN, (1024, 640))
    assert (small["width"], small["height"]) == (1024, 640)


def test_a_damaged_state_file_falls_back(tmp_path):
    bad = tmp_path / "window_state.json"
    bad.write_text("{not json")
    assert desktop._load_window_state(bad) is None
    assert desktop._restored_geometry({"x": "left"}, SCREEN, (1024, 640)) is None
    desktop._save_window_state({"x": 1, "y": 2, "width": 3, "height": 4}, bad)
    assert desktop._load_window_state(bad)["width"] == 3


def test_the_window_state_file_is_not_tracked():
    ignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text()
    assert "config/window_state.json" in ignore


# ---------------------------------------------------------------- the pages


def test_one_shortcut_sheet_for_every_page():
    js = (CONSOLE / "static" / "console.js").read_text(encoding="utf-8")
    assert "function wfShowShortcuts" in js and 'e.key !== "?"' in js
    for page in (CONSOLE / "pages").glob("*.html"):
        html = page.read_text(encoding="utf-8")
        assert "function showShortcuts" not in html, f"{page.name} builds its own sheet"
    detail = (CONSOLE / "pages" / "detail.html").read_text(encoding="utf-8")
    assert "window.WF_PAGE_SHORTCUTS" in detail


def test_the_job_poll_survives_a_background_tab():
    js = (CONSOLE / "static" / "console.js").read_text(encoding="utf-8")
    poll = js[js.index("async function pollJobs"):]
    poll = poll[:poll.index("\n}\n")]
    assert "setTimeout(pollJobs" in poll and "requestAnimationFrame" not in re.sub(r"//.*", "", poll)
