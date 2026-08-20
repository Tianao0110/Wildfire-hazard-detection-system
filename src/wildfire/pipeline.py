"""Batch orchestration for Layer 1: image -> detections -> annotated/grid-map -> ImageResult.

Runs every available detector (dead-tree primary + fire/smoke secondary) on each
image and merges their detections. Reports progress via a callback so both `tqdm`
and `gr.Progress` work. No risk classification — detections are flagged with the
image's GPS location.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Sequence

import cv2
import numpy as np

from .annotate import draw_boxes, grid_density_map
from .config import Settings
from .device import device_label, machine_name
from .gps import extract_altitude, extract_camera, extract_timestamp, get_location, mrk_location
from .imageio_utils import PASSTHROUGH_EXTS, load_rgb_uint8
from .risk import batch_stats
from .types import BatchResult, Detection, ImageResult

# progress(current, total, name)
ProgressFn = Optional[Callable[[int, int, str], None]]

JPEG_QUALITY = 88


def _detection_source(path: Path, rgb: np.ndarray, cache_dir: Path, max_mb: float = 0.0) -> str:
    """Path to feed the detectors.

    Common formats pass through unless they exceed `max_mb`, in which case a
    resolution-preserving JPEG (descending quality ladder) is cached and used —
    smaller files decode faster in every detector, which is where a 200-image
    flight spends its CPU time. TIFF/exotic formats are normalized the same way.
    `max_mb <= 0` disables compression (TIFFs then get a lossless PNG copy).
    """
    passthrough = path.suffix.lower() in PASSTHROUGH_EXTS
    limit = max_mb * 1024 * 1024
    if passthrough and (max_mb <= 0 or path.stat().st_size <= limit):
        return str(path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    if max_mb <= 0:  # legacy TIFF path: lossless normalize
        out = cache_dir / f"{path.stem}_det.png"
        cv2.imwrite(str(out), bgr)
        return str(out)
    out = cache_dir / f"{path.stem}_det.jpg"
    for quality in (85, 75, 65, 55, 45):
        ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if ok and (buf.nbytes <= limit or quality == 45):
            out.write_bytes(buf.tobytes())
            break
    return str(out)


def _save_jpg(path: Path, bgr: np.ndarray) -> str:
    cv2.imwrite(str(path), bgr, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    return str(path)


def _run_detectors(detectors: Sequence, image_src: str,
                   settings: Optional[Settings] = None) -> list[Detection]:
    """Run every detector on the image and concatenate detections.

    Applied here rather than inside a detector so it holds whatever produced the
    box - YOLO today, the ONNX dead-tree model when it lands - and so one place
    decides what the operator is shown.
    """
    merged: list[Detection] = []
    for det in detectors:
        merged.extend(det.predict(image_src))
    floors = getattr(settings, "class_conf_thresholds", None) or {}
    if floors:
        merged = [d for d in merged if d.score >= float(floors.get(d.display, 0) or 0)]
    return merged


def process_image(
    path: str | Path,
    detectors: Sequence,
    settings: Settings,
    out_dir: Path,
) -> ImageResult:
    """Process a single image end-to-end into an ImageResult (never raises)."""
    path = Path(path)
    name = path.name
    try:
        rgb = load_rgb_uint8(path)
        H, W = rgb.shape[:2]
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

        det_src = _detection_source(path, rgb, out_dir / "_cache",
                                    max_mb=settings.preprocess_max_mb)
        detections = _run_detectors(detectors, det_src, settings)

        # One subfolder per artifact kind — a 200-image flight would otherwise
        # dump 600 mixed files (original/annotated/gridmap) into the run root.
        stem = path.stem
        for sub in ("originals", "annotated", "gridmaps"):
            (out_dir / sub).mkdir(parents=True, exist_ok=True)
        orig_path = _save_jpg(out_dir / "originals" / f"{stem}.jpg", bgr)
        annotated_path = _save_jpg(out_dir / "annotated" / f"{stem}.jpg",
                                   draw_boxes(bgr, detections))
        density_path = _save_jpg(
            out_dir / "gridmaps" / f"{stem}.jpg",
            grid_density_map(bgr, detections, rows=settings.grid_rows, cols=settings.grid_cols),
        )

        # RTK .MRK beside the photos beats EXIF GPS (cm vs m grade) when present.
        rtk = mrk_location(path)
        gps = (rtk[0], rtk[1]) if rtk else get_location(path)
        altitude = rtk[2] if rtk and rtk[2] is not None else extract_altitude(path)

        return ImageResult(
            path=str(path), name=name, width=W, height=H,
            detections=detections,
            gps=gps, altitude=altitude, timestamp=extract_timestamp(path),
            camera=extract_camera(path),
            flagged=bool(detections),
            orig_display_path=orig_path, annotated_path=annotated_path, density_path=density_path,
        )
    except Exception as e:  # one bad image must not sink the batch
        return ImageResult(
            path=str(path), name=name, width=0, height=0,
            flagged=False, error=f"{type(e).__name__}: {e}",
        )


def run_batch(
    paths: Sequence[str | Path],
    detectors: Sequence,
    settings: Settings,
    progress: ProgressFn = None,
    out_dir: str | Path | None = None,
    batch_label: str = "",
) -> BatchResult:
    """Process a batch of images and return a BatchResult with aggregate stats."""
    out = Path(out_dir) if out_dir else settings.output_path
    out.mkdir(parents=True, exist_ok=True)

    total = len(paths)
    results: list[ImageResult] = []
    for i, p in enumerate(paths):
        res = process_image(p, detectors, settings, out)
        results.append(res)
        if progress:
            progress(i + 1, total, res.name)

    batch_info = {
        "batch_label": batch_label or out.name,
        "operator": settings.operator_name,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        # Which COMPUTER ran this, for the report and for cloud ownership. The
        # GPU string below answers "why was this fast/slow" and is diagnostics,
        # not identity - two laptops can carry the same card.
        "machine": machine_name(),
        "device": device_label(),
        "model_count": len(detectors),
        "conf_threshold": settings.conf_threshold,
        "slice_size": settings.slice_size,
        "image_count": total,
        "output_dir": str(out),
    }
    return BatchResult(images=results, stats=batch_stats(results), batch_info=batch_info)
