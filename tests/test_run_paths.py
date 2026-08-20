"""A finished run must survive the project folder being moved.

batch.json used to store absolute paths for the three derived images (the
display original, the annotated frame, the grid map). Those paths are a snapshot
of where the project sat the day the run was made, so moving the project - which
this one did, into SAIT/ - left every past run showing no imagery at all in the
console and printing "no imagery is stored for this run" in the PDF, while the
files sat untouched on disk.

Paths are now stored relative to the run folder. These tests pin both halves:
new runs are written relocatable, and runs written the old way still recover.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src.wildfire.types import (
    BatchResult, Detection, ImageResult, run_absolute, run_relative,
)


def _jpg(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.zeros((8, 8, 3), np.uint8)).save(path)
    return path


def _run(root: Path, run_id: str = "console_20260101_000000") -> tuple[Path, ImageResult]:
    """A run folder holding one image's three derived artifacts."""
    run_dir = root / run_id
    src = _jpg(root / "source" / "DJI_0001.JPG")
    im = ImageResult(
        path=str(src), name="DJI_0001.JPG", width=8, height=8,
        detections=[Detection(cls_name="dead_tree", display="Dead Tree",
                              score=0.9, xyxy=(1.0, 1.0, 4.0, 4.0))],
        gps=(51.0, -115.0), flagged=True,
        orig_display_path=str(_jpg(run_dir / "originals" / "DJI_0001.jpg")),
        annotated_path=str(_jpg(run_dir / "annotated" / "DJI_0001.jpg")),
        density_path=str(_jpg(run_dir / "gridmaps" / "DJI_0001.jpg")),
    )
    return run_dir, im


# ------------------------------------------------------------- the helpers


def test_derived_paths_are_stored_relative(tmp_path):
    run_dir, im = _run(tmp_path)
    d = im.to_dict(run_dir)
    assert d["annotated_path"] == "annotated/DJI_0001.jpg"
    assert d["orig_display_path"] == "originals/DJI_0001.jpg"
    assert d["density_path"] == "gridmaps/DJI_0001.jpg"


def test_the_source_photo_stays_absolute(tmp_path):
    """`path` points outside the run folder and identifies the image; it must
    not be rewritten."""
    run_dir, im = _run(tmp_path)
    assert Path(im.to_dict(run_dir)["path"]).is_absolute()


def test_roundtrip_through_a_moved_folder(tmp_path):
    run_dir, im = _run(tmp_path)
    stored = im.to_dict(run_dir)

    moved = tmp_path / "elsewhere" / run_dir.name
    moved.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(run_dir), str(moved))

    back = ImageResult.from_dict(stored, moved)
    assert back.annotated_path == str(moved / "annotated" / "DJI_0001.jpg")
    assert Path(back.annotated_path).exists()


def test_an_old_absolute_path_is_re_anchored(tmp_path):
    """The actual bug: a run written before this change, then relocated."""
    run_dir, _ = _run(tmp_path)
    stale = "/Users/someone/Desktop/Wildfire/outputs/console_20260101_000000/annotated/DJI_0001.jpg"
    assert run_absolute(stale, run_dir) == str(run_dir / "annotated" / "DJI_0001.jpg")


def test_an_absolute_path_that_still_resolves_is_left_alone(tmp_path):
    run_dir, im = _run(tmp_path)
    assert run_absolute(im.annotated_path, run_dir) == im.annotated_path


def test_unresolvable_paths_do_not_crash(tmp_path):
    run_dir, _ = _run(tmp_path)
    assert run_absolute(None, run_dir) is None
    assert run_relative(None, run_dir) is None
    # nothing to re-anchor on: returned unchanged rather than guessed at
    assert run_absolute("/nowhere/at/all.jpg", run_dir) == "/nowhere/at/all.jpg"
    # a source photo outside the run folder keeps its absolute form
    outside = str(tmp_path / "source" / "DJI_0001.JPG")
    assert run_relative(outside, run_dir) == outside


# --------------------------------------------------------------- end to end


def test_the_console_still_serves_a_run_after_the_project_moves(tmp_path):
    """The one that would have caught this: write a run, move the whole outputs
    tree, and ask the console for its imagery."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from src.wildfire.config import Settings
    from src.wildfire.console.server import create_app

    before = tmp_path / "before" / "outputs"
    run_dir, im = _run(before)
    batch = BatchResult(images=[im], stats={"images_processed": 1},
                        batch_info={"batch_label": run_dir.name})
    (run_dir / "batch.json").write_text(json.dumps(batch.to_dict(run_dir)),
                                        encoding="utf-8")

    after = tmp_path / "after" / "outputs"
    after.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(before), str(after))

    settings = Settings(output_dir=str(after), models_dir=str(tmp_path / "m"),
                        model_sources=[])
    client = TestClient(create_app(settings))
    detail = client.get(f"/api/scans/{run_dir.name}").json()

    image = detail["images_detail"][0]
    for key in ("original_url", "annotated_url", "gridmap_url"):
        assert image[key], f"{key} is empty after the move"
        assert client.get(image[key]).status_code == 200, f"{key} does not serve"
    assert detail["preview_urls"], "the scan list lost its preview"


# --------------------------------------------------- across the two platforms
# This project ships on Windows and macOS, and a run folder can be copied from
# one to the other. Absoluteness is not a portable property: "/Users/h/x.jpg"
# has no drive letter, so WindowsPath calls it relative, and "D:\out\x.jpg" is
# relative to PosixPath. Judging it with only the running platform's rules glued
# a foreign absolute path onto the run directory and produced nonsense. These
# run identically on either OS, which is the point.

RUN_ID = "console_20260101_000000"


def _foreign(tmp_path):
    run = tmp_path / RUN_ID
    _jpg(run / "annotated" / "DJI_0001.jpg")
    return run


@pytest.mark.parametrize("stored,note", [
    (f"/Users/h/Desktop/Wildfire/outputs/{RUN_ID}/annotated/DJI_0001.jpg", "macOS absolute"),
    (rf"D:\Sait\outputs\{RUN_ID}\annotated\DJI_0001.jpg", "Windows absolute"),
    (rf"C:\Users\h\OneDrive\Wildfire\outputs\{RUN_ID}\annotated\DJI_0001.jpg", "OneDrive path"),
])
def test_a_run_copied_from_the_other_platform_still_finds_its_imagery(
        tmp_path, stored, note):
    run = _foreign(tmp_path)
    resolved = run_absolute(stored, run)
    assert Path(resolved).exists(), f"{note} did not resolve"
    assert Path(resolved) == run / "annotated" / "DJI_0001.jpg"


@pytest.mark.parametrize("stored", ["annotated/DJI_0001.jpg", r"annotated\DJI_0001.jpg"])
def test_a_relative_path_resolves_whichever_separator_it_uses(tmp_path, stored):
    run = _foreign(tmp_path)
    assert Path(run_absolute(stored, run)).exists()


def test_what_gets_written_is_always_posix(tmp_path):
    """Forward slashes travel; backslashes do not. The store form must be the
    portable one whatever platform wrote it."""
    run = _foreign(tmp_path)
    stored = run_relative(str(run / "annotated" / "DJI_0001.jpg"), run)
    assert stored == "annotated/DJI_0001.jpg"
    assert "\\" not in stored


def test_a_foreign_path_with_no_run_folder_in_it_is_left_alone(tmp_path):
    """Nothing to re-anchor on: return it unchanged rather than inventing a
    path under the run directory."""
    run = _foreign(tmp_path)
    for stored in (r"D:\somewhere\else\x.jpg", "/somewhere/else/x.jpg"):
        assert run_absolute(stored, run) == stored
