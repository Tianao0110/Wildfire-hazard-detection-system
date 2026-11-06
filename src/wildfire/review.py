"""Human-in-the-loop review: turn detections into candidate proposals, let a human
confirm/reject, and rebuild outputs from only the confirmed ones.

Per the research, RGB detection cannot be trusted autonomously, so Layer-1
detections are treated as PROPOSALS. The reviewer confirms which are real; only
confirmed detections enter the report, and every accept/reject is saved as a label
for Phase-2 training.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

from .annotate import draw_boxes, grid_density_map
from .imageio_utils import load_rgb_uint8
from .risk import batch_stats
from .types import BatchResult, Detection, ImageResult

JPEG_Q = [cv2.IMWRITE_JPEG_QUALITY, 88]

# Review labels offered in the manual annotator + their box colors (RGB).
# Same palette as annotate.py / report.py / console.js -- see report.py for why
# smoke and dead tree are no longer orange and yellow.
REVIEW_LABELS = ["Dead Tree", "Fallen Tree", "Flame", "Smoke"]
REVIEW_COLORS = {
    "Dead Tree": (217, 205, 176),  # bone
    "Flame": (224, 85, 85),        # red
    "Smoke": (84, 110, 122),       # slate
    "Fallen Tree": (41, 182, 246),  # sky blue
}
DISPLAY_MAX = 1600  # downscale big images for the in-browser annotator


def to_annotator(im: ImageResult, disp_max: int = DISPLAY_MAX):
    """Return (display_rgb, boxes, scale) for the image_annotator.

    Boxes are the current detections in DISPLAY pixel coords; scale converts
    display coords back to original-image coords (orig = display * scale).
    """
    rgb = load_rgb_uint8(im.path)
    H, W = rgb.shape[:2]
    scale = max(1.0, max(H, W) / float(disp_max))
    disp = cv2.resize(rgb, (int(W / scale), int(H / scale))) if scale > 1 else rgb
    boxes = []
    for d in im.detections:
        x1, y1, x2, y2 = d.xyxy
        boxes.append({"xmin": int(x1 / scale), "ymin": int(y1 / scale),
                      "xmax": int(x2 / scale), "ymax": int(y2 / scale),
                      "label": d.display,
                      "color": REVIEW_COLORS.get(d.display, REVIEW_COLORS["Dead Tree"])})
    return disp, boxes, scale


def detections_from_boxes(boxes, scale: float) -> list[Detection]:
    """Convert annotator boxes (display coords) back to Detections (original coords)."""
    out: list[Detection] = []
    for b in boxes or []:
        try:
            x1, y1 = float(b["xmin"]) * scale, float(b["ymin"]) * scale
            x2, y2 = float(b["xmax"]) * scale, float(b["ymax"]) * scale
        except (KeyError, TypeError, ValueError):
            continue
        if x2 <= x1 or y2 <= y1:
            continue
        label = (b.get("label") or "Dead Tree").strip() or "Dead Tree"
        out.append(Detection(cls_name=label.lower().replace(" ", "_"), display=label,
                             score=1.0, xyxy=(x1, y1, x2, y2)))  # human-confirmed
    return out


def build_confirmed_from_annotations(batch: BatchResult, ann_state: dict, out_dir: Path) -> BatchResult:
    """Rebuild a BatchResult from the human-edited annotator boxes (per image)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    new_images: list[ImageResult] = []
    for i, im in enumerate(batch.images):
        st = ann_state.get(i) if ann_state else None
        dets = detections_from_boxes(st.get("boxes") if st else [], st.get("scale", 1.0) if st else 1.0)
        annotated_path, density_path = im.annotated_path, im.density_path
        try:
            bgr = cv2.cvtColor(load_rgb_uint8(im.path), cv2.COLOR_RGB2BGR)
            stem = Path(im.path).stem
            (out_dir / "annotated").mkdir(parents=True, exist_ok=True)
            (out_dir / "gridmaps").mkdir(parents=True, exist_ok=True)
            annotated_path = str(out_dir / "annotated" / f"{stem}_confirmed.jpg")
            cv2.imwrite(annotated_path, draw_boxes(bgr, dets), JPEG_Q)
            density_path = str(out_dir / "gridmaps" / f"{stem}_confirmed.jpg")
            cv2.imwrite(density_path, grid_density_map(bgr, dets), JPEG_Q)
        except Exception:
            pass
        new_images.append(replace(im, detections=dets, flagged=bool(dets),
                                  annotated_path=annotated_path, density_path=density_path))
    info = dict(batch.batch_info)
    info["review"] = "human-confirmed (annotator)"
    return BatchResult(images=new_images, stats=batch_stats(new_images), batch_info=info)


def save_review_labels(confirmed: BatchResult, path: str | Path) -> Path:
    """Save the human-confirmed boxes as a Phase-2 label file."""
    path = Path(path)
    records = []
    for im in confirmed.images:
        for d in im.detections:
            records.append({"image": im.path, "xyxy": [round(v, 1) for v in d.xyxy],
                            "class": d.display})
    path.write_text(json.dumps({"labels": records}, indent=2), encoding="utf-8")
    return path


def make_candidate_crops(batch: BatchResult, pad_frac: float = 0.7, thumb: int = 200):
    """Return (crops_rgb, meta). Each crop is a padded RGB thumbnail of one detection
    with the proposal box drawn; meta[i] = {image_index, det_index, name, display, score}.
    """
    crops: list[np.ndarray] = []
    meta: list[dict] = []
    cache: dict[str, np.ndarray | None] = {}

    for ii, im in enumerate(batch.images):
        if not im.detections:
            continue
        if im.path not in cache:
            try:
                cache[im.path] = load_rgb_uint8(im.path)
            except Exception:
                cache[im.path] = None
        rgb = cache[im.path]
        if rgb is None:
            continue
        H, W = rgb.shape[:2]
        for di, d in enumerate(im.detections):
            x1, y1, x2, y2 = d.xyxy
            bw, bh = max(1.0, x2 - x1), max(1.0, y2 - y1)
            px, py = bw * pad_frac, bh * pad_frac
            cx1, cy1 = int(max(0, x1 - px)), int(max(0, y1 - py))
            cx2, cy2 = int(min(W, x2 + px)), int(min(H, y2 + py))
            crop = rgb[cy1:cy2, cx1:cx2].copy()
            if crop.size == 0:
                continue
            cv2.rectangle(crop, (int(x1 - cx1), int(y1 - cy1)), (int(x2 - cx1), int(y2 - cy1)),
                          (255, 255, 0), max(1, crop.shape[1] // 80))
            scale = thumb / max(crop.shape[0], crop.shape[1])
            if scale < 1:
                crop = cv2.resize(crop, (int(crop.shape[1] * scale), int(crop.shape[0] * scale)))
            crops.append(crop)
            meta.append({"image_index": ii, "det_index": di, "name": im.name,
                         "display": d.display, "score": float(d.score)})
    return crops, meta


def render_review_thumb(crop_rgb: np.ndarray, accepted: bool) -> np.ndarray:
    """Add a green (accepted) or red (rejected) border to a candidate thumbnail."""
    color = (40, 200, 40) if accepted else (200, 40, 40)  # RGB
    bt = max(4, crop_rgb.shape[0] // 22)
    return cv2.copyMakeBorder(crop_rgb, bt, bt, bt, bt, cv2.BORDER_CONSTANT, value=color)


def build_confirmed_batch(batch: BatchResult, accepted: set[tuple[int, int]], out_dir: Path) -> BatchResult:
    """Rebuild a BatchResult keeping only confirmed (image_index, det_index) detections,
    re-rendering the annotated + grid map from the confirmed set.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    new_images: list[ImageResult] = []
    for ii, im in enumerate(batch.images):
        kept = [d for di, d in enumerate(im.detections) if (ii, di) in accepted]
        annotated_path = im.annotated_path
        density_path = im.density_path
        try:
            rgb = load_rgb_uint8(im.path)
            bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            stem = Path(im.path).stem
            annotated_path = str(out_dir / f"{stem}_confirmed.jpg")
            cv2.imwrite(annotated_path, draw_boxes(bgr, kept), JPEG_Q)
            density_path = str(out_dir / f"{stem}_confirmed_grid.jpg")
            cv2.imwrite(density_path, grid_density_map(bgr, kept), JPEG_Q)
        except Exception:
            pass
        new_images.append(replace(im, detections=kept, flagged=bool(kept),
                                  annotated_path=annotated_path, density_path=density_path))

    info = dict(batch.batch_info)
    info["review"] = "human-confirmed"
    return BatchResult(images=new_images, stats=batch_stats(new_images), batch_info=info)


def save_labels(batch: BatchResult, accepted: set[tuple[int, int]], path: str | Path) -> Path:
    """Save accept/reject decisions as a Phase-2 label file (image + box + verdict)."""
    path = Path(path)
    records = []
    for ii, im in enumerate(batch.images):
        for di, d in enumerate(im.detections):
            records.append({
                "image": im.path, "xyxy": [round(v, 1) for v in d.xyxy],
                "proposed_class": d.display, "score": round(float(d.score), 4),
                "confirmed": (ii, di) in accepted,
            })
    path.write_text(json.dumps({"labels": records}, indent=2), encoding="utf-8")
    return path
