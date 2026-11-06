"""SAHI (Slicing Aided Hyper Inference) + Ultralytics YOLO11 detector.

Wraps an ultralytics .pt (e.g. the fire/smoke model) behind the common detector
interface used by the pipeline: a `.predict(image) -> list[Detection]` method.
The DeepForest dead-tree detector implements the same interface separately.

Verified against sahi 0.12.1: model_type="ultralytics", the get_sliced_prediction
signature, and result.object_prediction_list parsing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

import numpy as np

from .config import ModelSource, Settings
from .device import pick_device
from .gps import downscale_factor
from .models import display_for
from .types import Detection

ImageInput = Union[str, Path, np.ndarray]


class SahiYoloDetector:
    """SAHI sliced inference over an Ultralytics YOLO11 .pt model."""

    def __init__(self, model_path: str | Path, settings: Settings, device: str | None = None,
                 source: ModelSource | None = None):
        from sahi import AutoDetectionModel

        self.name = Path(model_path).stem
        self.settings = settings
        # The configured source for this file, if any: per-model scale, tile size,
        # merge threshold and class map. A loose .pt dropped into models/ has none
        # and runs exactly as before.
        self.source = source
        self.model = AutoDetectionModel.from_pretrained(
            model_type="ultralytics",
            model_path=str(model_path),
            confidence_threshold=settings.conf_threshold,
            device=device or pick_device(),
        )

    def _display(self, name: str) -> str:
        """Display label for a raw class; "" means this source drops the class."""
        cmap = (self.source.class_map if self.source else None) or {}
        if name in cmap:
            return cmap[name] or ""
        return display_for(name)

    def _at_model_scale(self, image: ImageInput) -> tuple[ImageInput, float]:
        """(what to infer on, factor back to native pixels).

        Same reason as DeepForest's resampling: a model is only valid at the
        scale it was trained on. Done in memory - SAHI takes an array, and
        nothing downstream re-reads this frame from disk.
        """
        target = self.source.target_gsd_cm if self.source else 0.0
        if not target or not isinstance(image, (str, Path)):
            return (str(image) if isinstance(image, (str, Path)) else image), 1.0
        factor = downscale_factor(image, target)
        if factor == 1.0:
            return str(image), 1.0
        from PIL import Image as PILImage

        with PILImage.open(image) as im:
            im = im.convert("RGB")
            width = im.size[0]
            small = im.resize((max(1, round(im.size[0] / factor)),
                               max(1, round(im.size[1] / factor))), PILImage.LANCZOS)
        return np.asarray(small), width / small.size[0]

    def predict(self, image: ImageInput) -> list[Detection]:
        from sahi.predict import get_sliced_prediction

        img_arg, box_scale = self._at_model_scale(image)
        s = self.settings
        tile = (self.source.slice_size if self.source and self.source.slice_size
                else s.slice_size)
        merge = self.source.merge_ios if self.source else 0.5
        result = get_sliced_prediction(
            image=img_arg,
            detection_model=self.model,
            slice_height=tile,
            slice_width=tile,
            overlap_height_ratio=s.overlap_ratio,
            overlap_width_ratio=s.overlap_ratio,
            perform_standard_pred=s.perform_standard_pred,
            postprocess_type="GREEDYNMM",
            postprocess_match_metric="IOS",
            postprocess_match_threshold=merge,
            batch_size=s.batch_size,
            verbose=0,
        )
        detections: list[Detection] = []
        for pred in result.object_prediction_list:
            name = pred.category.name
            display = self._display(name)
            if not display:
                continue
            minx, miny, maxx, maxy = (v * box_scale for v in pred.bbox.to_xyxy())
            detections.append(
                Detection(
                    cls_name=str(name),
                    display=display,
                    score=float(pred.score.value),
                    xyxy=(float(minx), float(miny), float(maxx), float(maxy)),
                )
            )
        return detections


def build_yolo_detectors(model_paths: list, settings: Settings, device: str | None = None) -> list:
    """Build one SahiYoloDetector per .pt path, each with its configured source.

    A source that is switched off in Settings is skipped even if its file is in
    models/ - otherwise a downloaded model could never be turned off again,
    because the sweep below picks up every .pt it finds.
    """
    dev = device or pick_device()
    by_file = {src.filename: src for src in settings.model_sources if src.backend == "yolo"}
    detectors = []
    for p in model_paths:
        src = by_file.get(Path(p).name)
        if src is not None and not src.enabled:
            continue
        detectors.append(SahiYoloDetector(p, settings, dev, source=src))
    return detectors
