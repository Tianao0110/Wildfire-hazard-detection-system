"""Tests for the operations console: run discovery, severity, and the JSON API.

FastAPI endpoints are exercised with TestClient (no server, no network);
skipped automatically if fastapi/httpx are not installed.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from PIL import Image

from src.wildfire.config import Settings
from src.wildfire.console.data import (
    dashboard_summary, discover_scans, display_severity, model_status, scan_detail,
)


def _settings(tmp_path) -> Settings:
    return Settings(output_dir=str(tmp_path / "outputs"),
                    models_dir=str(tmp_path / "models"), model_sources=[])


def _make_run(tmp_path, name="console_20260710_120000", *, flame=0, smoke=0, dead=1,
              gps=(51.1, -115.4), labels=None):
    run = tmp_path / "outputs" / name
    run.mkdir(parents=True, exist_ok=True)
    img = run / "img_annotated.jpg"
    Image.fromarray(np.zeros((10, 10, 3), np.uint8)).save(img)
    dets = ([{"cls_name": "fire", "display": "Flame", "score": 0.9, "xyxy": [0, 0, 5, 5]}] * flame
            + [{"cls_name": "smoke", "display": "Smoke", "score": 0.8, "xyxy": [0, 0, 5, 5]}] * smoke
            + [{"cls_name": "dead_tree", "display": "Dead Tree", "score": 0.7, "xyxy": [0, 0, 5, 5]}] * dead)
    counts: dict = {}
    for d in dets:
        counts[d["display"]] = counts.get(d["display"], 0) + 1
    batch = {
        "batch_info": {"batch_label": name, "generated_at": "2026-07-10 12:00:00",
                       "device": "cpu", "model_count": 1, "conf_threshold": 0.3,
                       "slice_size": 1024, "image_count": 1},
        "stats": {"detections_by_type": counts, "total_detections": len(dets)},
        "images": [{"path": str(run / "img.jpg"), "name": "img.jpg", "width": 10, "height": 10,
                    "detections": dets, "gps": list(gps) if gps else None,
                    "flagged": bool(dets), "annotated_path": str(img)}],
    }
    (run / "batch.json").write_text(json.dumps(batch), encoding="utf-8")
    if labels is not None:
        (run / "labels.json").write_text(json.dumps(labels), encoding="utf-8")
    return run


# ------------------------------------------------------------------ severity
def test_display_severity_ladder():
    """The ladder itself, with thresholds passed in.

    Thresholds are calibration and get retuned when the detector changes, so
    they are given explicitly here - otherwise this test fails for a reason that
    has nothing to do with the logic it covers.
    """
    sev = lambda counts, n: display_severity(counts, n, dead_high=10, dead_medium=3)

    assert sev({"Flame": 1, "Dead Tree": 1}, 10) == "high"  # flame always escalates
    assert sev({"Dead Tree": 100}, 10) == "high"
    assert sev({"Dead Tree": 30}, 10) == "medium"
    assert sev({"Dead Tree": 5}, 10) == "low"
    assert sev({"Smoke": 1, "Dead Tree": 2}, 10) == "medium"  # smoke floors at medium
    assert sev({"Fallen Log": 1}, 10) == "low"
    assert sev({}, 10) is None
    assert sev({"Dead Tree": 12}, 0) == "high"  # zero images must not divide by zero


def test_scan_summary_reports_the_severity_mix_not_just_the_worst(tmp_path):
    """A run badge is its worst frame; the mix says what the rest look like.

    Without this, a flight with flame in one image out of many reads as though
    every image were on fire - which is exactly what the badge alone implied.
    """
    from src.wildfire.console.data import scan_summary

    settings = _settings(tmp_path)
    run = tmp_path / "console_20260101_000000"
    run.mkdir(parents=True)

    def frame(name, dead, flame=0):
        dets = [{"cls_name": "dead", "display": "Dead Tree", "score": 0.9,
                 "xyxy": [0, 0, 5, 5]} for _ in range(dead)]
        dets += [{"cls_name": "fire", "display": "Flame", "score": 0.8,
                  "xyxy": [0, 0, 5, 5]} for _ in range(flame)]
        return {"path": name, "name": name, "width": 10, "height": 10,
                "gps": None, "detections": dets, "flagged": bool(dets)}

    images = [frame("a.jpg", 5, flame=1),  # flame -> high, on its own
              frame("b.jpg", 45),          # >= 39 -> high
              frame("c.jpg", 20),          # >= 13 -> medium
              frame("d.jpg", 5),           # -> low
              frame("e.jpg", 0)]           # -> none
    (run / "batch.json").write_text(json.dumps({
        "batch_info": {"image_count": len(images)},
        "stats": {"detections_by_type": {"Dead Tree": 75, "Flame": 1}},
        "images": images,
    }), encoding="utf-8")

    s = scan_summary(run, settings)
    assert s["severity"] == "high"          # worst frame still drives the badge
    assert s["flame_images"] == 1           # ...but only one image has flame
    assert s["severity_mix"] == {"high": 2, "medium": 1, "low": 1, "none": 1}
    assert sum(s["severity_mix"].values()) == len(images)


def test_default_severity_thresholds_separate_real_densities():
    """The defaults must actually grade real imagery into more than one level.

    They did not: at 10 / 3 per image every frame of the May flight came out
    High and the badge was decoration. These are percentiles of 49 real frames
    run through the pipeline as it is now (crown model at its training scale,
    2..55 dead trees per frame, median 20), so a regression back to thresholds
    fitted to some other detector fails here.
    """
    low_frame = {"Dead Tree": 7}        # p10 of the real distribution
    typical = {"Dead Tree": 20}         # median
    dense = {"Dead Tree": 49}           # p95

    assert display_severity(low_frame, 1) == "low"
    assert display_severity(typical, 1) == "medium"
    assert display_severity(dense, 1) == "high"
    assert len({display_severity(c, 1) for c in (low_frame, typical, dense)}) == 3


# ------------------------------------------------------------------ discovery
def test_discover_scans_and_detail(tmp_path):
    settings = _settings(tmp_path)
    _make_run(tmp_path, "console_20260710_120000", flame=1)
    _make_run(tmp_path, "review_20260709_080000", dead=2, gps=None)
    (tmp_path / "outputs" / "_uploads").mkdir(parents=True)  # staging: ignored
    (tmp_path / "outputs" / "junk").mkdir()  # no batch/labels: ignored

    scans = discover_scans(settings)
    assert [s["id"] for s in scans] == ["console_20260710_120000", "review_20260709_080000"]
    assert scans[0]["severity"] == "high" and scans[0]["gps"] == [51.1, -115.4]
    assert scans[1]["severity"] == "low" and scans[1]["gps"] is None

    detail = scan_detail("console_20260710_120000", settings)
    assert detail["images_detail"][0]["annotated_url"].startswith("/outputs/console_20260710_120000/")
    assert detail["peak_confidence"] == 0.9
    assert scan_detail("nope", settings) is None


def test_reviewed_run_uses_confirmed_labels(tmp_path):
    settings = _settings(tmp_path)
    labels = {"labels": [
        {"image": "x.jpg", "xyxy": [0, 0, 5, 5], "class": "Dead Tree"},
        {"image": "x.jpg", "xyxy": [1, 1, 6, 6], "class": "Dead Tree"},
    ]}
    # Raw batch says Flame (would be high), reviewer confirmed only dead trees.
    _make_run(tmp_path, "review_20260710_090000", flame=3, dead=0, labels=labels)
    s = discover_scans(settings)[0]
    assert s["reviewed"] is True
    assert s["detections_by_type"] == {"Dead Tree": 2}
    assert s["severity"] == "low"


def test_dashboard_summary_counts(tmp_path):
    settings = _settings(tmp_path)
    _make_run(tmp_path, "a_20260710_100000", flame=1)
    _make_run(tmp_path, "b_20260710_110000", smoke=1, dead=0)
    _make_run(tmp_path, "c_20260710_120000", dead=0, gps=None)  # clean run
    d = dashboard_summary(settings)
    assert d["total_scans"] == 3 and d["high_risk"] == 1 and d["flagged_scans"] == 2
    assert d["severity_counts"] == {"high": 1, "medium": 1, "low": 0}
    assert len(d["pins"]) == 2  # only GPS-tagged runs with detections


# ------------------------------------------------------------------ models
def test_model_status_reports_onnx_missing(tmp_path):
    from src.wildfire.config import ModelSource

    settings = _settings(tmp_path)
    settings.model_sources = [
        ModelSource(key="deadtree_onnx", filename="dead_tree.onnx", backend="onnx"),
    ]
    (tmp_path / "models").mkdir(exist_ok=True)
    st = model_status(settings)
    assert st[0]["ready"] is False and "dead_tree.onnx" in st[0]["note"]


# ------------------------------------------------------------------ API
def test_api_endpoints(tmp_path):
    pytest.importorskip("fastapi")
    httpx = pytest.importorskip("httpx")  # noqa: F841 (TestClient dependency)
    from fastapi.testclient import TestClient

    from src.wildfire.console.server import create_app

    settings = _settings(tmp_path)
    _make_run(tmp_path, "console_20260710_120000", flame=1)
    client = TestClient(create_app(settings))

    r = client.get("/api/summary")
    assert r.status_code == 200 and r.json()["total_scans"] == 1

    r = client.get("/api/scans")
    assert r.status_code == 200
    assert r.json()["scans"][0]["id"] == "console_20260710_120000"

    r = client.get("/api/scans/console_20260710_120000")
    assert r.status_code == 200 and r.json()["severity"] == "high"
    assert client.get("/api/scans/missing").status_code == 404

    # pages + static artifacts are served
    assert client.get("/").status_code == 200
    assert client.get("/scans").status_code == 200
    assert client.get("/scans/console_20260710_120000").status_code == 200
    art = r.json()["images_detail"][0]["annotated_url"]
    assert client.get(art).status_code == 200

    # upload rejects unsupported files
    r = client.post("/api/detect", files=[("files", ("x.txt", b"nope", "text/plain"))])
    assert r.status_code == 400

    # new pages are served
    assert client.get("/review").status_code == 200
    assert client.get("/map").status_code == 200


def test_save_labels_endpoint(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from src.wildfire.console.server import create_app

    settings = _settings(tmp_path)
    _make_run(tmp_path, "console_20260710_120000", flame=2, dead=0)  # raw = high
    client = TestClient(create_app(settings))

    # reviewer deletes the flames, draws two dead trees + one degenerate box
    r = client.post("/api/scans/console_20260710_120000/labels", json={"labels": {
        "img.jpg": [
            {"xyxy": [1, 1, 4, 4], "class": "Dead Tree"},
            {"xyxy": [5, 5, 9, 9], "class": "Dead Tree"},
            {"xyxy": [7, 7, 3, 3], "class": "Dead Tree"},  # inverted -> dropped
        ],
    }})
    assert r.status_code == 200
    body = r.json()
    assert body["saved"] == 2
    detail = body["detail"]
    assert detail["reviewed"] is True
    assert detail["detections_by_type"] == {"Dead Tree": 2}
    assert detail["severity"] == "low"  # confirmed labels override the raw flames
    assert detail["images_detail"][0]["confirmed"] is not None

    labels = json.loads(
        (tmp_path / "outputs" / "console_20260710_120000" / "labels.json").read_text(encoding="utf-8"))
    assert len(labels["labels"]) == 2  # the training-set file

    # unknown image name is a client error
    r = client.post("/api/scans/console_20260710_120000/labels",
                    json={"labels": {"ghost.jpg": []}})
    assert r.status_code == 400


def test_image_reviewed_toggle(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from src.wildfire.console.server import create_app

    settings = _settings(tmp_path)
    _make_run(tmp_path, "console_20260710_120000", dead=1)
    client = TestClient(create_app(settings))
    rid = "console_20260710_120000"

    # nothing reviewed until the operator explicitly acts
    d = client.get(f"/api/scans/{rid}").json()
    assert d["reviewed_image_count"] == 0
    assert d["images_detail"][0]["reviewed_by_user"] is False

    r = client.post(f"/api/scans/{rid}/image-reviewed", json={"name": "img.jpg", "reviewed": True})
    assert r.status_code == 200 and r.json()["reviewed_image_count"] == 1
    d = client.get(f"/api/scans/{rid}").json()
    assert d["images_detail"][0]["reviewed_by_user"] is True and d["reviewed_image_count"] == 1
    # persisted to a sidecar file, not labels.json
    assert (tmp_path / "outputs" / rid / "reviewed_images.json").exists()

    # undo
    client.post(f"/api/scans/{rid}/image-reviewed", json={"name": "img.jpg", "reviewed": False})
    assert client.get(f"/api/scans/{rid}").json()["reviewed_image_count"] == 0

    # unknown image -> 400
    assert client.post(f"/api/scans/{rid}/image-reviewed",
                       json={"name": "ghost.jpg", "reviewed": True}).status_code == 400


def test_reports_and_settings_api(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from src.wildfire.config import ModelSource
    from src.wildfire.console.server import create_app

    settings = _settings(tmp_path)
    settings.model_sources = [ModelSource(key="deadtree_onnx", filename="x.onnx", backend="onnx")]
    run = _make_run(tmp_path, "console_20260710_120000", flame=1)
    (run / "report_20260710_120500.pdf").write_bytes(b"pdf-a")
    (run / "report_20260710_130500.pdf").write_bytes(b"pdf-b")
    client = TestClient(create_app(settings))

    r = client.get("/api/reports").json()["reports"]
    assert len(r) == 2 and r[0]["file"] == "report_20260710_130500.pdf"  # newest first
    assert r[0]["run_id"] == "console_20260710_120000" and r[0]["severity"] == "high"

    # settings roundtrip: whitelisted values cast+applied, unknown ignored
    r = client.post("/api/settings", json={"values": {
        "conf_threshold": "0.5", "slice_size": 512, "nonsense_key": 1,
        "onnx_normalize": "weird",  # falls back to a valid choice
    }, "model_enabled": {"deadtree_onnx": False}})
    assert r.status_code == 200
    applied = r.json()["applied"]
    assert applied["conf_threshold"] == 0.5 and applied["slice_size"] == 512
    assert "nonsense_key" not in applied
    assert settings.conf_threshold == 0.5
    assert settings.onnx_normalize == "0-255"
    assert settings.model_sources[0].enabled is False

    got = client.get("/api/settings").json()
    assert got["values"]["conf_threshold"] == 0.5
    assert got["models"][0]["enabled"] is False

    assert client.get("/reports").status_code == 200
    assert client.get("/settings").status_code == 200


def test_map_data_month_filter(tmp_path):
    from src.wildfire.console.data import map_data

    settings = _settings(tmp_path)
    _make_run(tmp_path, "june_20260610_100000", dead=2, gps=(51.10, -115.40))
    _make_run(tmp_path, "july_20260710_110000", dead=3, gps=(51.20, -115.30))

    d = map_data(settings)  # default: everything
    assert d["month"] == "all" and d["points"] == 2
    assert [m["key"] for m in d["months"]] == ["2026-07", "2026-06"]  # newest first

    d = map_data(settings, month="2026-06")
    assert d["points"] == 1 and d["sites"][0]["members"][0]["run_id"].startswith("june")

    d = map_data(settings, month="latest")
    assert d["month"] == "2026-07" and d["points"] == 1

    d = map_data(settings, month="2026")  # whole-year prefix
    assert d["points"] == 2


def test_extract_camera_from_exif(tmp_path):
    from PIL import Image as PILImage

    from src.wildfire.gps import extract_camera

    p = tmp_path / "dji.jpg"
    img = PILImage.fromarray(np.zeros((8, 8, 3), np.uint8))
    exif = PILImage.Exif()
    exif[271], exif[272] = "DJI", "FC3582"  # Make, Model
    img.save(p, exif=exif)
    assert extract_camera(p) == "DJI FC3582"

    q = tmp_path / "plain.jpg"
    PILImage.fromarray(np.zeros((8, 8, 3), np.uint8)).save(q)
    assert extract_camera(q) is None


def test_report_embeds_summary_map(tmp_path):
    from src.wildfire.report import build_report
    from src.wildfire.types import BatchResult, Detection, ImageResult

    img = tmp_path / "shot.jpg"
    Image.fromarray(np.zeros((40, 60, 3), np.uint8)).save(img)
    batch = BatchResult(
        images=[ImageResult(path=str(img), name="shot.jpg", width=60, height=40,
                            detections=[Detection("fire", "Flame", 0.9, (1, 1, 9, 9))],
                            gps=(51.1, -115.4), flagged=True,
                            orig_display_path=str(img), annotated_path=str(img),
                            density_path=str(img))],
        stats={"images_processed": 1, "total_detections": 1},
        batch_info={"batch_label": "t"})
    pdf = build_report(batch, tmp_path / "report_x.pdf")
    assert pdf.exists() and pdf.stat().st_size > 1000
    assert (tmp_path / "_report_assets" / "summary_map.png").exists()


def test_report_image_pages_capped():
    from src.wildfire.report import select_image_pages
    from src.wildfire.types import BatchResult, Detection, ImageResult

    def im(name, n_dets):
        return ImageResult(path=name, name=name, width=10, height=10,
                           detections=[Detection("d", "Dead Tree", 0.9, (0, 0, 5, 5))] * n_dets)

    batch = BatchResult(images=[im("a", 1), im("b", 9), im("c", 0), im("d", 5)],
                        stats={}, batch_info={})
    picked, note = select_image_pages(batch, cap=2)
    assert [i.name for i in picked] == ["b", "d"]  # hazard-densest first
    assert "top 2 of 4" in note
    picked, note = select_image_pages(batch, cap=10)
    assert len(picked) == 4 and note is None  # under the cap -> everything, no note


def test_report_paths_never_collide(tmp_path):
    from src.wildfire.report import latest_report, timestamped_report_path

    a = timestamped_report_path(tmp_path)
    a.write_bytes(b"pdf1")
    b = timestamped_report_path(tmp_path)  # same second -> suffixed, not overwritten
    assert a != b
    b.write_bytes(b"pdf2")
    assert latest_report(tmp_path) == b
    assert a.read_bytes() == b"pdf1"


def test_cluster_sites_dedups_nearby_images():
    from src.wildfire.console.data import cluster_sites

    pts = [
        {"lat": 51.10000, "lon": -115.40000, "severity": "low", "run_id": "a", "name": "1.jpg", "thumb": None},
        {"lat": 51.10010, "lon": -115.40010, "severity": "high", "run_id": "a", "name": "2.jpg", "thumb": None},
        {"lat": 51.12000, "lon": -115.40000, "severity": "low", "run_id": "b", "name": "3.jpg", "thumb": None},
    ]
    sites = cluster_sites(pts, radius_m=40)
    assert len(sites) == 2  # first two are ~15m apart -> one site
    near = next(s for s in sites if s["count"] == 2)
    assert near["severity"] == "high"  # max severity wins
    assert len(near["members"]) == 2


def test_cluster_sites_matches_the_naive_scan():
    """The grid index must not change the answer, only the speed.

    cluster_sites indexes sites into radius-sized cells; the obvious way to get
    that wrong is to miss a match across a cell boundary, or to attach a point to
    a later site when an earlier one also qualified. This pins the output against
    a plain scan over several geometries, including the awkward ones.
    """
    import random

    from src.wildfire.console.data import (KIND_ORDER, SEVERITY_ORDER,
                                           _haversine_m, cluster_sites)

    def naive(points, radius_m=40.0):
        rank = dict(SEVERITY_ORDER)
        sites = []
        for p in points:
            target = None
            for s in sites:  # first site in range wins, same as cluster_sites
                if _haversine_m(p["lat"], p["lon"], s["lat"], s["lon"]) <= radius_m:
                    target = s
                    break
            if target is None:
                sites.append({"lat": p["lat"], "lon": p["lon"], "severity": p["severity"],
                              "kind": p.get("kind", "deadtree"), "kinds": [], "count": 0,
                              "members": []})
                target = sites[-1]
            n = target["count"]
            target["lat"] = (target["lat"] * n + p["lat"]) / (n + 1)
            target["lon"] = (target["lon"] * n + p["lon"]) / (n + 1)
            target["count"] = n + 1
            if rank.get(p["severity"], 0) > rank.get(target["severity"], 0):
                target["severity"] = p["severity"]
            if KIND_ORDER.get(p.get("kind"), 0) > KIND_ORDER.get(target["kind"], 0):
                target["kind"] = p["kind"]
            # every hazard type at the site, most critical first
            for k in p.get("kinds") or [p.get("kind", "deadtree")]:
                if k not in target["kinds"]:
                    target["kinds"].append(k)
            target["kinds"].sort(key=lambda k: -KIND_ORDER.get(k, 0))
            target["members"].append({k: p[k] for k in ("run_id", "name", "severity", "thumb")
                                      if k in p})
        return sites

    # (label, count, degrees of spread, origin) - spread 0 means every point
    # identical; a big spread means nothing merges; high latitude stretches the
    # longitude cells; near 0 and near 180 sit on coordinate sign flips.
    cases = [
        ("dense overlap", 250, 0.001, (51.10, -115.40)),
        ("normal flight", 400, 0.02, (51.10, -115.40)),
        ("nothing merges", 300, 0.30, (51.10, -115.40)),
        ("all identical", 120, 0.0, (51.10, -115.40)),
        ("high latitude", 300, 0.02, (78.00, -115.40)),
        ("across equator", 300, 0.05, (-0.02, -115.40)),
        ("near antimeridian", 300, 0.05, (51.10, 179.98)),
    ]
    for label, n, spread, (lat0, lon0) in cases:
        rnd = random.Random(hash(label) & 0xFFFF)
        pts = [{"lat": lat0 + rnd.random() * spread, "lon": lon0 + rnd.random() * spread,
                "severity": rnd.choice(["high", "medium", "low"]),
                "kind": rnd.choice(["deadtree", "smoke", "flame"]),
                "run_id": f"r{i % 5}", "name": f"IMG_{i}.JPG", "thumb": None}
               for i in range(n)]
        assert cluster_sites([dict(p) for p in pts]) == naive([dict(p) for p in pts]), label

    assert cluster_sites([]) == []


def test_cluster_sites_stays_fast_at_scale():
    """A month can hold 10k+ photos and this runs inside a web request.

    The original nested scan took 7.5 s for 10k points, which froze the desktop
    window. Budget is deliberately loose (2 s vs the ~0.05 s measured) so the test
    reports an algorithmic regression, not CI jitter.
    """
    import random
    import time

    from src.wildfire.console.data import cluster_sites

    rnd = random.Random(99)
    pts = [{"lat": 51.10 + rnd.random() * 0.046, "lon": -115.40 + rnd.random() * 0.046,
            "severity": "low", "kind": "deadtree",
            "run_id": "r", "name": f"IMG_{i}.JPG", "thumb": None}
           for i in range(10000)]
    t0 = time.perf_counter()
    sites = cluster_sites(pts)
    elapsed = time.perf_counter() - t0
    assert len(sites) > 1000  # sanity: it really did cluster something
    assert elapsed < 2.0, f"cluster_sites took {elapsed:.2f}s for 10k points"


def test_detection_source_compresses_oversized(tmp_path):
    from src.wildfire.pipeline import _detection_source

    rng = np.random.default_rng(1)
    rgb = rng.integers(0, 255, (1200, 1600, 3), dtype=np.uint8)  # noise = big jpg
    src = tmp_path / "big.jpg"
    Image.fromarray(rgb).save(src, quality=98)
    assert src.stat().st_size > 100_000

    out = _detection_source(src, rgb, tmp_path / "_cache", max_mb=0.1)
    assert out.endswith("_det.jpg")
    from pathlib import Path as P
    assert P(out).stat().st_size < src.stat().st_size
    # small file passes through untouched
    small = _detection_source(src, rgb, tmp_path / "_cache", max_mb=50)
    assert small == str(src)


def test_pipeline_writes_sorted_subfolders(tmp_path):
    from src.wildfire.pipeline import process_image

    img = tmp_path / "shot.jpg"
    Image.fromarray(np.zeros((40, 60, 3), np.uint8)).save(img)
    out = tmp_path / "run"
    res = process_image(img, detectors=[], settings=Settings(model_sources=[]), out_dir=out)
    assert res.error is None
    assert (out / "originals" / "shot.jpg").exists()
    assert (out / "annotated" / "shot.jpg").exists()
    assert (out / "gridmaps" / "shot.jpg").exists()
    # only the three artifact folders in the run root — no loose jpg soup
    assert not list(out.glob("*.jpg"))
