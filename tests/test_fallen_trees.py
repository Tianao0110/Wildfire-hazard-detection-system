"""Fallen trees as a hazard type of their own, and the plumbing it exposed.

The crown model cannot see a tree lying on the ground - there is no crown - and
downed wood is surface fuel. A second detector (YOLOv8 deadwood, Mayra et al.
2025) adds it. Wiring it in found three older defects that these tests also pin:
the detection copy of every frame lost the EXIF the scale logic reads, a
settings.json that listed model sources hid every model added later, and the
Settings toggle for a model did nothing once its file was on disk.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src.wildfire.config import ModelSource, Settings, load_settings
from src.wildfire.console import data
from src.wildfire.gps import ground_sample_distance_cm
from src.wildfire.models import display_for

ROOT = Path(__file__).resolve().parents[1]
XMP = b'<x:xmpmeta><rdf:Description drone-dji:RelativeAltitude="+53.139"/></x:xmpmeta>'


def _dji_like(path: Path, size=(2000, 1500)) -> Path:
    im = Image.fromarray(np.random.default_rng(0).integers(0, 255, (size[1], size[0], 3),
                                                            dtype=np.uint8))
    exif = im.getexif()
    exif.get_ifd(0x8769)[41989] = 24  # FocalLengthIn35mmFilm
    im.save(path, exif=exif, xmp=XMP, quality=95)
    return path


# ------------------------------------------------------- the detection copy


def test_the_detection_copy_keeps_the_metadata_scale_depends_on(tmp_path):
    """Every real DJI frame is over the 2 MB pre-compression limit, so every
    detector reads the re-encoded copy. cv2 writes pixels only; without the
    splice the copy had no GSD and scale-aware detection silently ran at
    native scale on every real flight."""
    from src.wildfire.imageio_utils import load_rgb_uint8
    from src.wildfire.pipeline import _detection_source

    original = _dji_like(tmp_path / "DJI_0001.JPG")
    copy = _detection_source(original, load_rgb_uint8(original), tmp_path / "cache",
                             max_mb=0.01)  # force the re-encode
    assert Path(copy) != original, "the test must exercise the re-encode"
    assert ground_sample_distance_cm(copy) == pytest.approx(ground_sample_distance_cm(original))
    assert Image.open(copy).size == (2000, 1500)


def test_a_file_without_metadata_still_compresses(tmp_path):
    from src.wildfire.pipeline import _detection_source

    plain = tmp_path / "plain.jpg"
    Image.fromarray(np.zeros((400, 600, 3), np.uint8)).save(plain, quality=100)
    rgb = np.asarray(Image.open(plain).convert("RGB"))
    out = _detection_source(plain, rgb, tmp_path / "cache", max_mb=0.001)
    assert Image.open(out).size == (600, 400)


# ------------------------------------------------------------ model sources


def test_a_model_added_in_a_release_reaches_an_existing_install(tmp_path):
    """settings.json used to replace the default list outright."""
    cfg = tmp_path / "settings.json"
    cfg.write_text(json.dumps({"model_sources": [
        {"key": "firesmoke", "filename": "fire_smoke.pt", "enabled": False},
    ]}), encoding="utf-8")
    s = load_settings(cfg)
    keys = [m.key for m in s.model_sources]
    assert "deadwood" in keys, "a new default source must be appended"
    assert keys.count("firesmoke") == 1, "and an existing one not duplicated"
    assert next(m for m in s.model_sources if m.key == "firesmoke").enabled is False, \
        "the operator's choice to turn it off survives"


def test_the_deadwood_source_is_configured_for_its_training_scale():
    src = next(m for m in Settings().model_sources if m.key == "deadwood")
    assert src.backend == "yolo" and src.hf_repo_id == "mayrajeo/yolov8-deadwood"
    assert 4.0 <= src.target_gsd_cm <= 5.0, "trained at ~4-5 cm/px"
    assert src.slice_size == 640, "trained on 640 px chips"
    assert src.class_map == {"groundwood": "Fallen Tree", "uprightwood": ""}


def test_class_map_renames_and_drops(monkeypatch):
    from src.wildfire import detect

    det = object.__new__(detect.SahiYoloDetector)
    det.source = ModelSource(key="k", filename="f.pt",
                             class_map={"groundwood": "Fallen Tree", "uprightwood": ""})
    assert det._display("groundwood") == "Fallen Tree"
    assert det._display("uprightwood") == "", "standing dead trees come from the crown model"
    assert det._display("fire") == "Flame", "unlisted classes still go through display_for"


def test_a_model_switched_off_in_settings_does_not_run(monkeypatch, tmp_path):
    """Every .pt in models/ is swept up, so the toggle used to be decorative."""
    from src.wildfire import detect

    built = []
    monkeypatch.setattr(detect.SahiYoloDetector, "__init__",
                        lambda self, p, s, d=None, source=None: built.append(Path(p).name))
    s = Settings(model_sources=[
        ModelSource(key="a", filename="a.pt", enabled=True),
        ModelSource(key="b", filename="b.pt", enabled=False),
    ])
    detect.build_yolo_detectors([tmp_path / "a.pt", tmp_path / "b.pt", tmp_path / "loose.pt"],
                                s, "cpu")
    assert built == ["a.pt", "loose.pt"], "off means off; a loose file still runs as before"


def test_display_rules_do_not_match_inside_words():
    assert display_for("groundwood") == "Fallen Tree"
    assert display_for("Fallen Log") == "Fallen Tree", "the legacy review label maps across"
    for word in ("dialog", "catalog", "logo"):
        assert display_for(word) != "Fallen Tree"


# ----------------------------------------------------------------- severity


def test_fallen_trees_grade_on_their_own_ladder():
    sev = lambda c: data.display_severity(c, 1, dead_high=10, dead_medium=5,
                                          fallen_high=40, fallen_medium=20)
    assert sev({"Fallen Tree": 45}) == "high"
    assert sev({"Fallen Tree": 25}) == "medium"
    assert sev({"Fallen Tree": 3}) == "low"
    assert sev({"Fallen Tree": 25, "Dead Tree": 12}) == "high", "the worse ladder wins"


def test_a_zero_fallen_threshold_leaves_them_ungraded():
    assert data.display_severity({"Fallen Tree": 500}, 1, 80, 45, 0, 0) == "low"


def test_the_per_image_mix_follows_the_review_verdict(tmp_path):
    """The mix bar read raw proposals, so a flame deleted in review coloured it."""
    run = tmp_path / "console_20260101_000000"
    run.mkdir()
    (run / "batch.json").write_text(json.dumps({"batch_info": {}, "stats": {}, "images": [{
        "path": "/s/a.JPG", "name": "a.JPG", "width": 9, "height": 9, "flagged": True,
        "detections": [{"display": "Flame", "score": 0.9, "xyxy": [1, 1, 5, 5]}]}]}))
    s = Settings(output_dir=str(tmp_path), models_dir=str(tmp_path), model_sources=[])
    assert data.scan_summary(run, s)["severity_mix"]["high"] == 1
    (run / "labels.json").write_text(json.dumps({"labels": [], "reviewed_images": ["/s/a.JPG"]}))
    assert data.scan_summary(run, s)["severity_mix"]["high"] == 0


# ---------------------------------------------------------------------- map


def test_a_site_lists_every_type_so_the_filter_can_find_it(tmp_path):
    run = tmp_path / "console_20260101_000000"
    run.mkdir()
    (run / "batch.json").write_text(json.dumps({"batch_info": {}, "stats": {}, "images": [{
        "path": "/s/a.JPG", "name": "a.JPG", "width": 9, "height": 9, "flagged": True,
        "gps": [51.1, -115.4], "timestamp": "2026:01:01 12:00:00",
        "detections": [{"display": "Dead Tree", "score": 0.9, "xyxy": [1, 1, 5, 5]},
                       {"display": "Fallen Tree", "score": 0.9, "xyxy": [5, 5, 9, 9]}]}]}))
    s = Settings(output_dir=str(tmp_path), models_dir=str(tmp_path), model_sources=[])
    site = data.map_data(s)["sites"][0]
    assert site["kind"] == "deadtree", "the pin colour is still the most critical type"
    assert site["kinds"] == ["deadtree", "fallen"], "but the fallen trees are not hidden"


# ------------------------------------------------------------------ palette


def test_the_fallen_colour_is_the_same_in_all_four_places():
    """AGENTS.md: the palette lives in console.css, console.js, annotate.py and
    report.py, and drifting apart is how the box and the pin disagreed before."""
    hexval = "29B6F6"
    css = (ROOT / "src/wildfire/console/static/console.css").read_text(encoding="utf-8")
    js = (ROOT / "src/wildfire/console/static/console.js").read_text(encoding="utf-8")
    assert re.search(rf"--kind-fallen:\s*#{hexval}", css, re.I)
    assert re.search(rf'fallen:\s*\{{[^}}]*color:\s*"#{hexval}"', js, re.I)

    from src.wildfire.annotate import COLOR_BY_DISPLAY
    from src.wildfire.report import FALLEN_RGB
    r, g, b = (int(hexval[i:i + 2], 16) for i in (0, 2, 4))
    assert FALLEN_RGB == (r, g, b)
    assert COLOR_BY_DISPLAY["Fallen Tree"] == (b, g, r), "OpenCV is BGR"


def test_default_fallen_thresholds_separate_real_densities():
    """Percentiles of 49 real frames (3..109 fallen trees, median 17): the
    defaults must grade them into all three levels, like the dead-tree pair."""
    sev = lambda n: data.display_severity({"Fallen Tree": n}, 1)
    assert sev(4) == "low"       # p10
    assert sev(17) == "medium"   # median
    assert sev(66) == "high"     # p95
