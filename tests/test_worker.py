"""Tests for the detection worker PROCESS.

The worker is the piece that actually runs detection, in its own low-priority
process, and the console only ever learns what happened by polling the progress
file it writes. That file is the whole contract, and until now nothing tested
it: a worker that died without writing "error" would leave the Scans page
spinning on "running" forever, and the suite would stay green.

Detection itself is not exercised here - `detectors` and `pipeline` are stubbed
so these run in milliseconds without torch. What is pinned is the protocol.
"""

from __future__ import annotations

import json
import sys
import types

import pytest

from src.wildfire.console import worker


def _spec(tmp_path, **extra) -> str:
    """Write a job spec and return its path, the way jobs.py does."""
    run_dir = tmp_path / "run"
    spec = {"run_dir": str(run_dir), "paths": [str(tmp_path / "a.jpg"),
                                               str(tmp_path / "b.jpg")]}
    spec.update(extra)
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    return str(spec_file)


def _progress(tmp_path) -> dict:
    return json.loads((tmp_path / "run" / "_progress.json").read_text(encoding="utf-8"))


def _stub_pipeline(monkeypatch, run_batch):
    """Stand in for the two modules the worker imports lazily, so no torch."""
    detectors = types.ModuleType("src.wildfire.detectors")
    detectors.build_detectors = lambda settings, log=None: ["fake-detector"]
    pipeline = types.ModuleType("src.wildfire.pipeline")
    pipeline.run_batch = run_batch
    monkeypatch.setitem(sys.modules, "src.wildfire.detectors", detectors)
    monkeypatch.setitem(sys.modules, "src.wildfire.pipeline", pipeline)


class _Batch:
    """Records the run_dir it is serialized against - batch.json stores the
    derived-image paths relative to it, so the worker must pass it through."""

    def __init__(self):
        self.serialized_against = "NOT CALLED"

    def to_dict(self, run_dir=None):
        self.serialized_against = run_dir
        return {"images": [], "stats": {}, "batch_info": {}}


# ------------------------------------------------------------ progress file


def test_write_progress_shape_and_no_temp_file_left(tmp_path):
    target = tmp_path / "_progress.json"
    worker._write_progress(target, "running", done=3, total=9, current="DJI_0042.JPG")

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "state": "running", "done": 3, "total": 9,
        "current": "DJI_0042.JPG", "error": None,
    }
    # written via a temp file and renamed, so a poller never reads a half file
    assert list(tmp_path.glob("*.tmp")) == []


def test_write_progress_overwrites_rather_than_appends(tmp_path):
    target = tmp_path / "_progress.json"
    worker._write_progress(target, "running", done=1, total=2)
    worker._write_progress(target, "done", done=2, total=2)
    assert json.loads(target.read_text(encoding="utf-8"))["state"] == "done"


# ------------------------------------------------------------------- main


def test_main_reports_running_then_done_and_writes_the_batch(tmp_path, monkeypatch):
    seen = []

    made = []

    def run_batch(paths, detectors, settings, progress=None, out_dir=None,
                  batch_label=None):
        seen.append({"paths": list(paths), "out_dir": out_dir, "label": batch_label})
        progress(1, 2, "a.jpg")
        assert _progress(tmp_path)["current"] == "a.jpg"  # visible WHILE running
        made.append(_Batch())
        return made[-1]

    _stub_pipeline(monkeypatch, run_batch)
    monkeypatch.setattr(sys, "argv", ["worker", _spec(tmp_path)])

    assert worker.main() == 0
    final = _progress(tmp_path)
    assert final["state"] == "done" and final["done"] == final["total"] == 2
    assert final["error"] is None
    assert seen[0]["label"] == "run" and len(seen[0]["paths"]) == 2
    assert json.loads((tmp_path / "run" / "batch.json").read_text(encoding="utf-8"))
    # serialized against the run folder, so the paths inside stay relocatable
    assert made[0].serialized_against == tmp_path / "run"


def test_main_records_the_error_instead_of_dying_silently(tmp_path, monkeypatch):
    """The one that matters: a crashed worker must not leave the page spinning."""
    def run_batch(*a, **k):
        raise RuntimeError("CUDA out of memory")

    _stub_pipeline(monkeypatch, run_batch)
    monkeypatch.setattr(sys, "argv", ["worker", _spec(tmp_path)])

    assert worker.main() == 1
    final = _progress(tmp_path)
    assert final["state"] == "error"
    assert "RuntimeError" in final["error"] and "CUDA out of memory" in final["error"]


def test_main_reports_a_failure_to_even_load_the_models(tmp_path, monkeypatch):
    detectors = types.ModuleType("src.wildfire.detectors")

    def boom(settings, log=None):
        raise FileNotFoundError("fire_smoke.pt")

    detectors.build_detectors = boom
    monkeypatch.setitem(sys.modules, "src.wildfire.detectors", detectors)
    monkeypatch.setitem(sys.modules, "src.wildfire.pipeline",
                        types.ModuleType("src.wildfire.pipeline"))
    monkeypatch.setattr(sys, "argv", ["worker", _spec(tmp_path)])

    assert worker.main() == 1
    assert _progress(tmp_path)["state"] == "error"


def test_main_creates_the_run_directory(tmp_path, monkeypatch):
    _stub_pipeline(monkeypatch, lambda *a, **k: _Batch())
    monkeypatch.setattr(sys, "argv", ["worker", _spec(tmp_path)])
    assert not (tmp_path / "run").exists()
    worker.main()
    assert (tmp_path / "run").is_dir()


# --------------------------------------------------------------- settings


def test_settings_come_from_the_spec_snapshot(tmp_path):
    """The job must use what the UI showed when it was queued, not today's file."""
    settings = worker._settings_from({"settings": {
        "conf_threshold": 0.77,
        "slice_size": 512,
        "not_a_setting": "ignored",  # forward compatibility, must not explode
        "model_sources": [{"key": "firesmoke", "filename": "fire_smoke.pt",
                           "backend": "yolo", "bogus": 1}],
    }})
    assert settings.conf_threshold == 0.77
    assert settings.slice_size == 512
    assert [m.key for m in settings.model_sources] == ["firesmoke"]
    assert not hasattr(settings, "not_a_setting")


def test_settings_fall_back_to_the_config_file(monkeypatch):
    from src.wildfire import config

    sentinel = object()
    monkeypatch.setattr(config, "load_settings", lambda: sentinel)
    assert worker._settings_from({}) is sentinel


def test_lower_priority_survives_a_host_that_refuses(monkeypatch):
    import os

    def refuse(_):
        raise OSError("not permitted")

    monkeypatch.setattr(os, "nice", refuse)
    worker._lower_priority()  # must not raise; a normal-priority run beats no run
