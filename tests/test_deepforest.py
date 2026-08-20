"""Integration test for the DeepForest dead-tree detector.

Loads the crown detector + alive/dead classifier (weights cached after first run)
and runs them on DeepForest's bundled NEON forest sample. The sample is a healthy
stand, so the expected result is zero "Dead Tree" detections — the point is to
verify the adapter loads (incl. the CropModel weight repair) and runs without
crashing. Auto-skips if deepforest is not installed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("deepforest")

from src.wildfire.config import load_settings  # noqa: E402
from src.wildfire.deepforest_detector import DeepForestDeadTreeDetector  # noqa: E402


def test_deepforest_dead_tree_runs_on_sample():
    from deepforest import get_data

    settings = load_settings()
    det = DeepForestDeadTreeDetector(settings)
    # The classifier must have loaded (CropModel repair worked).
    assert det._crop is not None

    dets = det.predict(get_data("OSBS_029.png"))
    assert isinstance(dets, list)
    for d in dets:
        assert d.display == "Dead Tree"
        assert 0.0 <= d.score <= 1.0


def test_crowns_are_filtered_before_the_classifier_runs():
    """The classifier must only see crowns this run would actually report.

    predict_tile(crop_model=...) classifies every crown it finds and `predict`
    then discards the ones below conf_threshold. On a real DJI frame that was
    1,406 crowns for 270 keepers - about 17 of 44 seconds spent on trees the run
    never reports. This asserts the ordering, not the timing: the crowns handed
    to _classify are exactly the ones at or above the threshold.
    """
    from deepforest import get_data

    settings = load_settings()
    det = DeepForestDeadTreeDetector(settings)
    seen = {}
    real = det._classify

    def spy(crowns, image_path):
        seen["given"] = crowns
        return real(crowns, image_path)

    det._classify = spy
    det.predict(get_data("OSBS_029.png"))

    given = seen.get("given")
    if given is None:
        pytest.skip("no crown cleared the threshold on this sample")
    assert len(given) > 0
    assert (given["score"] >= settings.conf_threshold).all(), \
        "a crown below conf_threshold was sent to the classifier"


def test_falls_back_when_deepforest_moves_the_private_helper():
    """_classify uses a private DeepForest helper. If a future version moves it,
    the detector must get slower, not stop finding dead trees."""
    from deepforest import get_data

    settings = load_settings()
    det = DeepForestDeadTreeDetector(settings)
    said = []
    det._say = said.append
    det._classify = lambda crowns, image_path: None  # simulate the helper vanishing

    dets = det.predict(get_data("OSBS_029.png"))
    assert isinstance(dets, list)  # still answers, via predict_tile(crop_model=...)
    for d in dets:
        assert d.display == "Dead Tree"
