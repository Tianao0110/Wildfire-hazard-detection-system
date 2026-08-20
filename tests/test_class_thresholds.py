"""A confidence floor per hazard type.

One global conf_threshold cannot serve both detectors. Dead trees come from a
crown model and 0.30 is reasonable there; flame and smoke come from a model
trained on fire photographed from the ground, and a nadir drone frame at
1.4 cm/px is a different visual domain, so sunlit dead grass reads as flame.
Measured over 36 fire-free frames from real flights: at 0.30 the model emitted
19 false flames and 11 false smokes across 16 of them; with a 0.60 floor, two
flames on two frames and no smoke at all.

The floor is applied where detections are merged, so it holds for whatever
produced the box rather than for one backend.
"""

from __future__ import annotations

import pytest

from src.wildfire.config import Settings
from src.wildfire.pipeline import _run_detectors
from src.wildfire.types import Detection


class _Fake:
    """A detector that returns a fixed list, ignoring the image."""

    name = "fake"

    def __init__(self, dets):
        self._dets = dets

    def predict(self, _image):
        return list(self._dets)


def _det(display, score):
    return Detection(cls_name=display.lower(), display=display, score=score,
                     xyxy=(1.0, 1.0, 9.0, 9.0))


def _settings(floors):
    return Settings(class_conf_thresholds=floors, model_sources=[])


def test_the_floor_drops_weak_flames_and_keeps_strong_ones():
    dets = [_det("Flame", 0.32), _det("Flame", 0.59), _det("Flame", 0.61),
            _det("Flame", 0.90)]
    kept = _run_detectors([_Fake(dets)], "x.jpg", _settings({"Flame": 0.60}))
    assert [round(d.score, 2) for d in kept] == [0.61, 0.90]


def test_each_class_gets_its_own_floor():
    dets = [_det("Flame", 0.55), _det("Smoke", 0.55), _det("Dead Tree", 0.55)]
    kept = _run_detectors([_Fake(dets)], "x.jpg",
                          _settings({"Flame": 0.60, "Smoke": 0.50}))
    assert sorted(d.display for d in kept) == ["Dead Tree", "Smoke"]


def test_a_class_without_a_floor_is_untouched():
    """Dead trees must keep behaving exactly as before this existed."""
    dets = [_det("Dead Tree", 0.31), _det("Dead Tree", 0.99)]
    kept = _run_detectors([_Fake(dets)], "x.jpg", _settings({"Flame": 0.60}))
    assert len(kept) == 2


def test_no_floors_configured_means_no_filtering():
    dets = [_det("Flame", 0.31), _det("Smoke", 0.31)]
    for floors in ({}, None):
        assert len(_run_detectors([_Fake(dets)], "x.jpg", _settings(floors or {}))) == 2
    assert len(_run_detectors([_Fake(dets)], "x.jpg")) == 2  # no settings at all


def test_a_zero_floor_falls_back_to_the_global_threshold():
    dets = [_det("Flame", 0.31)]
    kept = _run_detectors([_Fake(dets)], "x.jpg", _settings({"Flame": 0.0}))
    assert len(kept) == 1


def test_the_floor_holds_across_detectors():
    """Two backends, one decision about what the operator sees."""
    a, b = _Fake([_det("Flame", 0.40)]), _Fake([_det("Flame", 0.80)])
    kept = _run_detectors([a, b], "x.jpg", _settings({"Flame": 0.60}))
    assert [round(d.score, 2) for d in kept] == [0.80]


def test_the_default_settings_carry_a_flame_and_smoke_floor():
    s = Settings(model_sources=[])
    assert s.class_conf_thresholds["Flame"] >= 0.5
    assert s.class_conf_thresholds["Smoke"] >= 0.5
    assert "Dead Tree" not in s.class_conf_thresholds


def test_the_worker_snapshot_carries_the_floors():
    """The job must run with the floors the UI showed when it was queued."""
    from src.wildfire.console import worker

    built = worker._settings_from({"settings": {
        "class_conf_thresholds": {"Flame": 0.75}, "conf_threshold": 0.3,
        "model_sources": [],
    }})
    assert built.class_conf_thresholds == {"Flame": 0.75}


def test_the_settings_api_clamps_and_rejects_junk():
    pytest.importorskip("fastapi")
    from src.wildfire.console.server import _EDITABLE_SETTINGS

    coerce = _EDITABLE_SETTINGS["class_conf_thresholds"]
    assert coerce({"Flame": "0.8"}) == {"Flame": 0.8}
    assert coerce({"Flame": 5}) == {"Flame": 0.99}
    assert coerce({"Flame": -1}) == {"Flame": 0.0}
    assert coerce("not a dict") == {}
    assert coerce({"Flame": [1, 2]}) == {}  # a non-number is dropped, not crashed
