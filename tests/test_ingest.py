"""Tests for mission-folder ingestion: image-only scanning, time grouping,
session labels, and the /api/source endpoints."""

from __future__ import annotations

import json
import os
from datetime import datetime

import numpy as np
import pytest
from PIL import Image

from src.wildfire.console.ingest import (
    SCAN_CACHE_KEEP, folder_signature, group_sessions, image_time,
    load_scan_cache, save_scan_cache, scan_source, time_of_day,
)


def _img(path, when: datetime):
    Image.fromarray(np.zeros((8, 8, 3), np.uint8)).save(path)
    ts = when.timestamp()
    os.utime(path, (ts, ts))  # no EXIF -> image_time falls back to mtime
    return path


def test_time_of_day_buckets():
    assert time_of_day(datetime(2026, 7, 8, 6)) == "Morning"
    assert time_of_day(datetime(2026, 7, 8, 12)) == "Midday"
    assert time_of_day(datetime(2026, 7, 8, 15)) == "Afternoon"
    assert time_of_day(datetime(2026, 7, 8, 20)) == "Evening"
    assert time_of_day(datetime(2026, 7, 8, 2)) == "Night"


def test_scan_source_images_only_and_sorted(tmp_path):
    _img(tmp_path / "b.jpg", datetime(2026, 7, 8, 9, 30))
    _img(tmp_path / "a.jpg", datetime(2026, 7, 8, 9, 0))
    (tmp_path / "flight.SRT").write_text("telemetry", encoding="utf-8")
    (tmp_path / "log.DAT").write_bytes(b"\x00")
    sub = tmp_path / "100MEDIA"
    sub.mkdir()
    _img(sub / "c.jpg", datetime(2026, 7, 8, 10, 0))

    result = scan_source(tmp_path)
    assert [im["name"] for im in result["images"]] == ["a.jpg", "b.jpg", "c.jpg"]
    assert result["ignored"] == 2  # sidecars counted, never opened


def test_group_sessions_by_gap_and_day(tmp_path):
    times = [
        datetime(2026, 7, 8, 6, 24), datetime(2026, 7, 8, 6, 40),   # morning flight
        datetime(2026, 7, 8, 14, 5), datetime(2026, 7, 8, 14, 20),  # afternoon flight (gap > 45m)
        datetime(2026, 7, 9, 9, 0),                                  # next day
    ]
    images = [{"path": f"p{i}.jpg", "name": f"p{i}.jpg", "time": t}
              for i, t in enumerate(times)]
    sessions = group_sessions(images, gap_minutes=45)
    assert len(sessions) == 3
    assert sessions[0]["day"] == "Jul 09, 2026" and sessions[0]["count"] == 1  # newest first
    assert sessions[1]["part"] == "Afternoon" and sessions[1]["count"] == 2
    assert sessions[2]["part"] == "Morning"
    assert sessions[2]["start"] == "06:24" and sessions[2]["end"] == "06:40"
    assert sessions[2]["paths"] == ["p0.jpg", "p1.jpg"]


def test_image_time_falls_back_to_mtime(tmp_path):
    when = datetime(2026, 7, 8, 6, 24, 30)
    p = _img(tmp_path / "x.jpg", when)
    assert abs((image_time(p) - when).total_seconds()) < 2


def test_source_api_roundtrip(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from src.wildfire.config import Settings
    from src.wildfire.console.server import create_app

    mission = tmp_path / "mission"
    mission.mkdir()
    _img(mission / "a.jpg", datetime(2026, 7, 8, 6, 24))
    _img(mission / "b.jpg", datetime(2026, 7, 8, 6, 40))
    (mission / "flight.SRT").write_text("x", encoding="utf-8")

    settings = Settings(output_dir=str(tmp_path / "outputs"),
                        models_dir=str(tmp_path / "models"), model_sources=[])
    client = TestClient(create_app(settings))

    assert client.get("/api/source").json()["sessions"] == []  # nothing configured

    r = client.post("/api/source", json={"folder": str(mission)})
    assert r.status_code == 200
    body = r.json()
    assert body["images"] == 2 and body["ignored"] == 1
    assert len(body["sessions"]) == 1
    s = body["sessions"][0]
    assert s["part"] == "Morning" and s["count"] == 2
    assert "paths" not in s  # absolute paths stay server-side
    assert settings.source_dir == str(mission)  # persisted on the settings object

    # bad folder -> 400; unknown session -> 404
    assert client.post("/api/source", json={"folder": str(tmp_path / "nope")}).status_code == 400
    assert client.post("/api/detect-session", json={"session": "junk"}).status_code == 404

    # review status endpoint answers (value depends on whether 7860 is in use)
    st = client.get("/api/review/status").json()
    assert "running" in st and st["url"].startswith("http://127.0.0.1")


# --------------------------------------------------------- change signature
# folder_signature runs on EVERY /api/source request, so it stats directories
# rather than files. These pin the two things that buys us: it must stay cheap,
# and it must still notice the changes a mission folder actually makes.


def test_folder_signature_stable_when_nothing_changes(tmp_path):
    _img(tmp_path / "a.jpg", datetime(2026, 7, 8, 9, 0))
    assert folder_signature(tmp_path) == folder_signature(tmp_path)


def test_folder_signature_notices_added_and_removed_files(tmp_path):
    _img(tmp_path / "a.jpg", datetime(2026, 7, 8, 9, 0))
    before = folder_signature(tmp_path)

    _img(tmp_path / "b.jpg", datetime(2026, 7, 8, 9, 30))
    added = folder_signature(tmp_path)
    assert added != before

    (tmp_path / "b.jpg").unlink()
    assert folder_signature(tmp_path) != added


def test_folder_signature_notices_a_new_subfolder(tmp_path):
    _img(tmp_path / "a.jpg", datetime(2026, 7, 8, 9, 0))
    before = folder_signature(tmp_path)
    sub = tmp_path / "101MEDIA"
    sub.mkdir()
    _img(sub / "c.jpg", datetime(2026, 7, 8, 10, 0))
    assert folder_signature(tmp_path) != before


def test_folder_signature_ignores_hidden_directories(tmp_path):
    """A .Trashes write must not invalidate a scan that never read it."""
    _img(tmp_path / "a.jpg", datetime(2026, 7, 8, 9, 0))
    hidden = tmp_path / ".Trashes"
    hidden.mkdir()
    before = folder_signature(tmp_path)  # after the hidden dir exists
    (hidden / "junk.bin").write_bytes(b"\x00")  # bumps .Trashes, not the root
    assert folder_signature(tmp_path) == before


def test_folder_signature_survives_a_missing_folder(tmp_path):
    assert folder_signature(tmp_path / "gone") == (0, 0.0)


# ------------------------------------------------------------- scan cache
# The scan result outlives the process, so relaunching the console does not
# repeat the EXIF pass. A cache that cannot be trusted must cost a rescan,
# never a wrong flight list.


def _scan(tmp_path):
    _img(tmp_path / "a.jpg", datetime(2026, 7, 8, 6, 24))
    _img(tmp_path / "b.jpg", datetime(2026, 7, 8, 6, 40))
    (tmp_path / "flight.SRT").write_text("x", encoding="utf-8")
    return scan_source(tmp_path)


def test_scan_cache_roundtrip(tmp_path):
    mission = tmp_path / "mission"
    mission.mkdir()
    result = _scan(mission)
    cache = tmp_path / ".scan_cache.json"
    sig = folder_signature(mission)

    save_scan_cache(cache, str(mission), sig, result)
    loaded = load_scan_cache(cache, str(mission), sig)

    assert loaded is not None
    assert loaded["ignored"] == result["ignored"]
    assert [im["name"] for im in loaded["images"]] == [im["name"] for im in result["images"]]
    for got, want in zip(loaded["images"], result["images"]):
        assert got["path"] == want["path"]
        assert abs((got["time"] - want["time"]).total_seconds()) < 1


def test_scan_cache_rejects_a_stale_signature(tmp_path):
    mission = tmp_path / "mission"
    mission.mkdir()
    result = _scan(mission)
    cache = tmp_path / ".scan_cache.json"
    save_scan_cache(cache, str(mission), folder_signature(mission), result)

    _img(mission / "c.jpg", datetime(2026, 7, 8, 7, 0))  # folder moved on
    assert load_scan_cache(cache, str(mission), folder_signature(mission)) is None


def test_scan_cache_rejects_unknown_folder_and_bad_file(tmp_path):
    cache = tmp_path / ".scan_cache.json"
    assert load_scan_cache(cache, "/nope", (0, 0.0)) is None  # no file yet

    cache.write_text("{ this is not json", encoding="utf-8")
    assert load_scan_cache(cache, "/nope", (0, 0.0)) is None  # half-written

    cache.write_text('{"version": 999, "folders": {}}', encoding="utf-8")
    assert load_scan_cache(cache, "/nope", (0, 0.0)) is None  # older layout


def test_scan_cache_keeps_only_the_recent_folders(tmp_path):
    cache = tmp_path / ".scan_cache.json"
    empty = {"folder": "x", "ignored": 0, "images": []}
    for i in range(SCAN_CACHE_KEEP + 2):
        save_scan_cache(cache, f"/folder/{i}", (i, 0.0), empty)

    kept = json.loads(cache.read_text(encoding="utf-8"))["folders"]
    assert len(kept) == SCAN_CACHE_KEEP
    assert "/folder/0" not in kept  # oldest evicted
    assert f"/folder/{SCAN_CACHE_KEEP + 1}" in kept  # newest kept


def test_second_launch_reuses_the_cache_instead_of_rescanning(tmp_path, monkeypatch):
    """The point of the whole thing: a restart must not repeat the EXIF pass."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from src.wildfire.config import Settings
    from src.wildfire.console import ingest as ingest_mod
    from src.wildfire.console.server import create_app

    mission = tmp_path / "mission"
    mission.mkdir()
    _scan(mission)

    scans = []
    real = ingest_mod.scan_source

    def counting(folder, progress=None):
        scans.append(str(folder))
        return real(folder, progress=progress)

    monkeypatch.setattr(ingest_mod, "scan_source", counting)

    def fresh_client():
        s = Settings(output_dir=str(tmp_path / "outputs"),
                     models_dir=str(tmp_path / "models"), model_sources=[])
        s.source_dir = str(mission)
        return TestClient(create_app(s))

    first = fresh_client().get("/api/source").json()
    assert len(scans) == 1  # cold: nothing cached anywhere

    second = fresh_client().get("/api/source").json()  # new app, new memory cache
    assert len(scans) == 1, "the disk cache should have answered this launch"
    assert second["images"] == first["images"] == 2
    assert [x["id"] for x in second["sessions"]] == [x["id"] for x in first["sessions"]]
