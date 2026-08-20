"""Plain data structures passed between Layer 1 modules.

Kept dependency-free (stdlib only) so any module can import them cheaply.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Detection:
    """A single object detection from the SAHI + YOLO pipeline."""

    cls_name: str  # raw model class name, e.g. "fire" / "smoke" (casing varies)
    display: str  # human label: "Flame" / "Smoke" / raw name
    score: float  # confidence 0..1
    xyxy: tuple[float, float, float, float]  # (minx, miny, maxx, maxy), original-image pixels

    @property
    def area(self) -> float:
        minx, miny, maxx, maxy = self.xyxy
        return max(0.0, maxx - minx) * max(0.0, maxy - miny)

    def to_dict(self) -> dict:
        return {
            "cls_name": self.cls_name,
            "display": self.display,
            "score": round(float(self.score), 4),
            "xyxy": [round(float(v), 1) for v in self.xyxy],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Detection":
        return cls(
            cls_name=d["cls_name"], display=d["display"],
            score=float(d["score"]), xyxy=tuple(d["xyxy"]),
        )


# ---------------------------------------------------------------- run paths
# The three derived images (original copy, annotated, grid map) always live
# INSIDE the run folder, so batch.json stores them RELATIVE to it. An absolute
# path is a snapshot of where the project sat the day the run was made: move the
# project and every past run loses its imagery, in the console AND in the PDF,
# while the files sit untouched on disk. That is not hypothetical - it happened
# when this project moved into SAIT/, and it is the same bug class as the
# desktop launcher baking in its own absolute path.
#
# `path` is deliberately NOT converted: it points at the operator's source photo
# outside the run folder, and it is what identifies an image across re-runs.


def run_relative(path: Optional[str], run_dir: Path) -> Optional[str]:
    """Store form: a derived image, relative to the run folder that holds it."""
    if not path:
        return path
    try:
        return Path(path).resolve().relative_to(Path(run_dir).resolve()).as_posix()
    except (ValueError, OSError):
        return path  # outside the run folder: keep whatever it is


def run_absolute(stored: Optional[str], run_dir: Path) -> Optional[str]:
    """Read form: an absolute path under THIS run folder, wherever it now sits.

    Three cases, in order: written relative (current), written absolute and
    still there (an old run whose project never moved), and written absolute but
    gone - re-anchored on the run folder name so runs made before this change
    recover their imagery instead of silently showing none.
    """
    if not stored:
        return stored
    p = Path(stored)
    run_dir = Path(run_dir)
    if not p.is_absolute():
        return str(run_dir / p)
    if p.exists():
        return stored
    parts = p.parts
    if run_dir.name in parts:
        tail = Path(*parts[parts.index(run_dir.name) + 1:])
        if tail.parts:
            return str(run_dir / tail)
    return stored


@dataclass
class ImageResult:
    """Everything Layer 1 produces for one input image.

    No risk classification (per spec): detections are simply flagged with the
    image's GPS location. `flagged` is True when the image has any detection;
    the map places one pin per detection at this image's GPS coordinate.
    """

    path: str
    name: str
    width: int
    height: int
    detections: list[Detection] = field(default_factory=list)
    gps: Optional[tuple[float, float]] = None  # (lat, lon) decimal degrees
    altitude: Optional[float] = None  # meters
    timestamp: Optional[str] = None  # capture time if available
    camera: Optional[str] = None  # drone/camera model from EXIF (e.g. "DJI FC3582")
    flagged: bool = False  # has at least one detection (a hazard location)
    orig_display_path: Optional[str] = None
    annotated_path: Optional[str] = None
    density_path: Optional[str] = None  # grid density (hazard-count) map
    error: Optional[str] = None

    def to_dict(self, run_dir: Optional[Path] = None) -> dict:
        """`run_dir` stores the derived images relative to it - pass it whenever
        writing batch.json, so the run stays readable after the folder moves."""
        rel = (lambda p: run_relative(p, run_dir)) if run_dir else (lambda p: p)
        return {
            "path": self.path,
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "detections": [d.to_dict() for d in self.detections],
            "gps": list(self.gps) if self.gps else None,
            "altitude": self.altitude,
            "timestamp": self.timestamp,
            "camera": self.camera,
            "flagged": self.flagged,
            "orig_display_path": rel(self.orig_display_path),
            "annotated_path": rel(self.annotated_path),
            "density_path": rel(self.density_path),
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict, run_dir: Optional[Path] = None) -> "ImageResult":
        """`run_dir` resolves the derived images back to absolute paths, so every
        consumer downstream keeps seeing what it always saw."""
        gps = d.get("gps")
        absolute = (lambda p: run_absolute(p, run_dir)) if run_dir else (lambda p: p)
        return cls(
            path=d["path"], name=d["name"], width=d.get("width", 0), height=d.get("height", 0),
            detections=[Detection.from_dict(x) for x in d.get("detections", [])],
            gps=tuple(gps) if gps else None,
            altitude=d.get("altitude"), timestamp=d.get("timestamp"),
            camera=d.get("camera"),
            flagged=d.get("flagged", False),
            orig_display_path=absolute(d.get("orig_display_path")),
            annotated_path=absolute(d.get("annotated_path")),
            density_path=absolute(d.get("density_path")),
            error=d.get("error"),
        )


@dataclass
class BatchResult:
    """A whole batch of processed images plus aggregate statistics."""

    images: list[ImageResult] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    batch_info: dict = field(default_factory=dict)

    def to_dict(self, run_dir: Optional[Path] = None) -> dict:
        return {
            "batch_info": self.batch_info,
            "stats": self.stats,
            "images": [im.to_dict(run_dir) for im in self.images],
        }

    @classmethod
    def from_dict(cls, d: dict, run_dir: Optional[Path] = None) -> "BatchResult":
        return cls(
            images=[ImageResult.from_dict(x, run_dir) for x in d.get("images", [])],
            stats=d.get("stats", {}),
            batch_info=d.get("batch_info", {}),
        )

