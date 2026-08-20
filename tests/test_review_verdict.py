"""A deleted detection must stay deleted, everywhere.

labels.json records the boxes a reviewer CONFIRMED, so it holds positives only.
That made "reviewed this image and there was nothing" indistinguishable from
"never opened this image", and both fell back to the model's proposals. An
operator who found a false flame, deleted it and saved, then went back and found
it still there - on the detail page, on its thumbnail, and as a pin on the map,
where it also drove the hazard-type filter.

The fix is `reviewed_images`: the set of images a save covered, whether or not
any box survived. These tests pin every surface that reads it.
"""

from __future__ import annotations

import json

import pytest

from src.wildfire.config import Settings
from src.wildfire.console import data


def _run(tmp_path, *, gps=(51.0, -115.0), display="Flame"):
    run = tmp_path / "console_20260101_000000"
    run.mkdir(parents=True)
    (run / "batch.json").write_text(json.dumps({
        "batch_info": {}, "stats": {},
        "images": [{
            "path": "/src/DJI_0001.JPG", "name": "DJI_0001.JPG",
            "width": 100, "height": 100, "gps": list(gps), "flagged": True,
            "timestamp": "2026:01:01 12:00:00",
            "detections": [{"cls_name": display.lower(), "display": display,
                            "score": 0.4, "xyxy": [1, 1, 9, 9]}],
        }],
    }), encoding="utf-8")
    settings = Settings(output_dir=str(tmp_path), models_dir=str(tmp_path),
                        model_sources=[])
    return run, settings


def _verdict(run, boxes, covered=("/src/DJI_0001.JPG",)):
    payload = {"labels": boxes}
    if covered is not None:
        payload["reviewed_images"] = list(covered)
    (run / "labels.json").write_text(json.dumps(payload), encoding="utf-8")


# ------------------------------------------------------- the reported bug


def test_a_deleted_detection_leaves_the_map(tmp_path):
    run, settings = _run(tmp_path)
    assert len(data.map_data(settings)["sites"]) == 1, "the proposal should start on the map"

    _verdict(run, [])  # the reviewer deleted the false flame and saved
    assert data.map_data(settings)["sites"] == []


def test_a_deleted_detection_leaves_the_detail_page(tmp_path):
    run, settings = _run(tmp_path)
    _verdict(run, [])
    image = data.scan_detail(run.name, settings)["images_detail"][0]
    assert image["confirmed"] == [], "[] means reviewed-and-cleared, None means unreviewed"
    assert image["counts"] == {}, "the thumbnail colour reads counts"


def test_an_unreviewed_image_still_shows_its_proposals(tmp_path):
    """The other half: silently hiding hazards nobody looked at is worse."""
    run, settings = _run(tmp_path)
    _verdict(run, [], covered=[])  # a save that covered some OTHER image
    image = data.scan_detail(run.name, settings)["images_detail"][0]
    assert image["confirmed"] is None
    assert len(data.map_data(settings)["sites"]) == 1


def test_labels_written_before_this_fix_keep_their_old_meaning(tmp_path):
    """An old file cannot say which images were opened, so it must not be read
    as if every image had been cleared."""
    run, settings = _run(tmp_path)
    _verdict(run, [], covered=None)  # no reviewed_images key at all
    assert data.scan_detail(run.name, settings)["images_detail"][0]["confirmed"] is None
    assert len(data.map_data(settings)["sites"]) == 1


# ------------------------------------------------------- kept detections


def test_a_confirmed_box_survives_and_keeps_its_class(tmp_path):
    run, settings = _run(tmp_path, display="Flame")
    _verdict(run, [{"image": "/src/DJI_0001.JPG", "xyxy": [1, 1, 9, 9],
                    "class": "Dead Tree"}])

    image = data.scan_detail(run.name, settings)["images_detail"][0]
    assert image["counts"] == {"Dead Tree": 1}, \
        "a confirmed box carries `class`, not `display`"

    sites = data.map_data(settings)["sites"]
    assert len(sites) == 1
    assert sites[0]["kind"] == "deadtree", \
        "the reviewer reclassified it, so the pin and the filter must follow"


def test_the_save_endpoint_records_what_it_covered(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from src.wildfire.console.server import create_app

    run, settings = _run(tmp_path)
    client = TestClient(create_app(settings))
    r = client.post(f"/api/scans/{run.name}/labels",
                    json={"labels": {"DJI_0001.JPG": []}})  # emptied, then saved
    assert r.status_code == 200 and r.json()["saved"] == 0

    written = json.loads((run / "labels.json").read_text(encoding="utf-8"))
    assert written["labels"] == []
    assert written["reviewed_images"] == ["/src/DJI_0001.JPG"], \
        "an emptied image must still be recorded as reviewed"
    assert r.json()["detail"]["images_detail"][0]["confirmed"] == []
