"""Detection flagging and batch statistics.

Per spec there is NO risk classification — we simply detect hazards and flag
their locations. This module reports per-image flags and batch-level counts by
detection type (Dead Tree / Fallen Tree / Flame / Smoke).
"""

from __future__ import annotations

from collections import Counter
from typing import Iterable

from .types import Detection, ImageResult


def is_flagged(detections: list[Detection]) -> bool:
    """True if the image has at least one detection (a hazard location)."""
    return len(detections) > 0


def batch_stats(results: Iterable[ImageResult]) -> dict:
    """Aggregate statistics across a processed batch."""
    results = list(results)
    n = len(results)
    class_counts: Counter = Counter()
    total_detections = 0
    with_deadtree = with_fallen = with_flame = with_smoke = with_gps = flagged = 0

    for r in results:
        total_detections += len(r.detections)
        names = {d.display for d in r.detections}
        if "Dead Tree" in names:
            with_deadtree += 1
        if "Fallen Tree" in names:
            with_fallen += 1
        if "Flame" in names:
            with_flame += 1
        if "Smoke" in names:
            with_smoke += 1
        if r.gps is not None:
            with_gps += 1
        if r.detections:
            flagged += 1
        for d in r.detections:
            class_counts[d.display] += 1

    return {
        "images_processed": n,
        "flagged_images": flagged,
        "total_detections": total_detections,
        "mean_detections_per_image": round(total_detections / n, 2) if n else 0.0,
        "detections_by_type": dict(class_counts),
        "images_with_deadtree": with_deadtree,
        "images_with_fallen": with_fallen,
        "images_with_flame": with_flame,
        "images_with_smoke": with_smoke,
        "images_with_gps": with_gps,
    }
