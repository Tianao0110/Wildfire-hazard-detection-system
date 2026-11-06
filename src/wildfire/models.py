"""YOLO model management: class->display mapping, discovery, and download manager.

Phase 1 detects hazardous DEAD TREES (primary) plus FLAME and SMOKE (secondary).
There may be one multi-class model or several single-purpose models in models/.
The pipeline runs every available model and merges detections; this module maps
each raw class name to a display label ("Dead Tree" / "Fallen Tree" / "Flame" /
"Smoke") so colors
and statistics are consistent regardless of which model produced a detection.

Download manager:
  firesmoke -> Hugging Face leeyunjai/yolo11-firedetect (firedetect-11s.pt),
               fallback GitHub Flare Guard best_nano_111.pt (verified, zero-auth).
  deadtree  -> source TBD (no turnkey model confirmed yet); drop a .pt in models/
               or train one and set its source in config.
"""

from __future__ import annotations

import shutil
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from .config import ModelSource, Settings

# Ordered (display_label, keywords). First match wins.
DISPLAY_RULES: list[tuple[str, tuple[str, ...]]] = [
    # Before "Dead Tree": a fallen tree is dead too, and "deadwood" would claim it.
    # No bare "log": matching is by substring, and "log" sits inside "catalog",
    # "logo" and "dialog".
    ("Fallen Tree", ("fallen", "groundwood", "downed", "down_wood")),
    ("Dead Tree", ("dead", "snag", "standing_dead", "standing-dead", "deadwood", "dry_tree", "dry-tree")),
    ("Flame", ("flame", "fire")),
    ("Smoke", ("smoke",)),
]

ProgressFn = Optional[Callable[[str], None]]


def display_for(name: str) -> str:
    """Map a raw model class name to a display label."""
    low = str(name).lower()
    for label, keywords in DISPLAY_RULES:
        if any(k in low for k in keywords):
            return label
    return str(name).replace("_", " ").title()


def _load_yolo_names(weights_path: Path) -> Optional[dict]:
    """Load a .pt and return its class-name dict, or None if it can't be read."""
    try:
        from ultralytics import YOLO

        return YOLO(str(weights_path)).names
    except Exception:
        return None


def available_model_files(settings: Settings) -> list[Path]:
    """All .pt files currently present in the models directory (sorted)."""
    models_dir = settings.models_path
    if not models_dir.is_dir():
        return []
    return sorted(p for p in models_dir.glob("*.pt"))


# --------------------------------------------------------------------- download
def _download_huggingface(source: ModelSource, dest: Path, log: ProgressFn) -> bool:
    if not (source.hf_repo_id and source.hf_filename):
        return False
    try:
        from huggingface_hub import hf_hub_download

        if log:
            log(f"[{source.key}] downloading {source.hf_filename} from Hugging Face ({source.hf_repo_id})...")
        cached = hf_hub_download(repo_id=source.hf_repo_id, filename=source.hf_filename)
        shutil.copyfile(cached, dest)
        return True
    except Exception as e:
        if log:
            log(f"[{source.key}] Hugging Face download failed ({e}); trying fallback...")
        return False


def _download_url(source: ModelSource, dest: Path, log: ProgressFn) -> bool:
    if not source.fallback_url:
        return False
    try:
        if log:
            log(f"[{source.key}] downloading from {source.fallback_url}...")
        urllib.request.urlretrieve(source.fallback_url, dest)
        return True
    except Exception as e:
        if log:
            log(f"[{source.key}] fallback download failed: {e}")
        return False


def download_source(settings: Settings, source: ModelSource, log: ProgressFn = None) -> Path:
    """Download one model source into models/, validating it loads as a YOLO model."""
    settings.ensure_dirs()
    dest = settings.model_path_for(source.filename)
    if not (source.hf_repo_id or source.fallback_url):
        raise RuntimeError(
            f"[{source.key}] has no download source configured. Place a YOLO .pt at "
            f"{dest} (or set hf_repo_id/fallback_url in config)."
        )

    tmp = dest.with_name(dest.stem + ".download.pt")  # keep .pt so ultralytics can validate
    ok = _download_huggingface(source, tmp, log) or _download_url(source, tmp, log)
    if not ok:
        raise RuntimeError(f"[{source.key}] could not be downloaded from any configured source.")

    names = _load_yolo_names(tmp)
    if not names:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"[{source.key}] downloaded file is not a loadable YOLO model.")

    tmp.replace(dest)
    if log:
        log(f"[{source.key}] ready: {dest.name}  classes={list(names.values())}")
    return dest


def ensure_yolo_sources(settings: Settings, log: ProgressFn = None) -> list[Path]:
    """Download missing YOLO (.pt) model sources; return all .pt paths in models/.

    Only handles backend="yolo" sources. DeepForest models auto-download their own
    weights on load and are not .pt files, so they are skipped here. Does NOT raise
    on an empty result — the detector assembly decides whether enough detectors exist.
    """
    settings.ensure_dirs()
    for source in settings.model_sources:
        if not source.enabled or source.backend != "yolo":
            continue
        dest = settings.model_path_for(source.filename)
        if dest.exists():
            if log:
                log(f"[{source.key}] using existing {dest.name}")
            continue
        if source.hf_repo_id or source.fallback_url:
            try:
                download_source(settings, source, log)
            except Exception as e:
                if log:
                    log(f"[{source.key}] skipped: {e}")
        elif log:
            log(f"[{source.key}] no source configured and no local file - skipping.")

    return available_model_files(settings)
