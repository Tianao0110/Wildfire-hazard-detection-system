"""Read-side data for the operations console: discover run folders under
outputs/, summarize them for the dashboard/scans/detail pages, and report
detector/model status.

Severity note: the console shows a High/Medium/Low badge that is DISPLAY-ONLY —
batch.json, labels.json and the PDF report carry no risk field (per spec there
is no formal risk classification). Dead trees are the primary target (most
scans detect nothing else), so the ladder is driven by dead-tree density:

    Flame present                       -> High   (fire is always urgent)
    dead trees / image >= high thr.     -> High   (default 10, settings)
    Smoke present                       -> Medium (possible fire nearby)
    dead trees / image >= medium thr.   -> Medium (default 3, settings)
    any other detection                 -> Low
    nothing                             -> no badge

Density (per image, not per run) keeps a 200-image flight comparable with a
10-image one. NOTE: overlapping flight photos re-count the same trees, so the
density is inflated but consistent; see the dedup roadmap before trusting
absolute numbers. A later phase may refine this with the local LLM / model.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..config import Settings
from ..types import run_absolute

SEVERITY_ORDER = {"high": 3, "medium": 2, "low": 1}


def display_severity(counts_by_type: dict, image_count: int,
                     dead_high: float = 80.0, dead_medium: float = 45.0) -> Optional[str]:
    """UI-only severity: dead-tree density ladder with flame/smoke escalation.

    Thresholds are calibrated, not guessed: real frames hold 27..102 dead trees
    (median 60), so the original 10 / 3 graded everything High. 80 / 45 are about
    the 85th and 22nd percentiles. Recalibrate when the detector changes.
    """
    if counts_by_type.get("Flame"):
        return "high"
    dead_per_image = counts_by_type.get("Dead Tree", 0) / max(1, image_count)
    if dead_per_image >= dead_high:
        return "high"
    if counts_by_type.get("Smoke") or dead_per_image >= dead_medium:
        return "medium"
    if sum(counts_by_type.values()):
        return "low"
    return None


def _severity_mix(images: list[dict], settings: Settings) -> tuple[dict, int]:
    """Grade each frame separately: how many high / medium / low / clear.

    A run badge can only report its worst frame, and that is the right answer to
    "is there anything urgent in here" - but on its own it is misleading. One
    real 58-image flight reads HIGH off 6 flame frames while the other 52 are 14
    high, 28 medium and 16 low. Showing the mix beside the badge is what stops
    "flame in 6 images" from looking like "58 bad images".
    """
    hi, med = settings.severity_deadtrees_high, settings.severity_deadtrees_medium
    mix = {"high": 0, "medium": 0, "low": 0, "none": 0}
    flame_images = 0
    for im in images:
        counts: dict[str, int] = {}
        for d in im.get("detections") or []:
            counts[d.get("display", "?")] = counts.get(d.get("display", "?"), 0) + 1
        if counts.get("Flame"):
            flame_images += 1
        mix[display_severity(counts, 1, hi, med) or "none"] += 1
    return mix, flame_images


def _parse_run_timestamp(run_name: str) -> Optional[datetime]:
    """Run folders end with _YYYYmmdd_HHMMSS (review_..., console_..., etc.)."""
    m = re.search(r"(\d{8})_(\d{6})$", run_name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def _load_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _counts_from_labels(labels: dict) -> dict:
    counts: dict[str, int] = {}
    for rec in labels.get("labels", []):
        if isinstance(rec, dict) and rec.get("confirmed") is not False:
            tag = rec.get("class") or rec.get("proposed_class") or "Dead Tree"
            counts[tag] = counts.get(tag, 0) + 1
    return counts


def review_verdict(run_dir: Path) -> tuple[dict[str, list[dict]], Optional[set[str]]]:
    """(confirmed boxes per image path, the image paths the review covered).

    labels.json holds positives only, so "reviewed and nothing confirmed" and
    "never opened" look identical in it. `reviewed_images` disambiguates them
    and is what stops a deleted detection reappearing. It is None for files
    written before that key existed: callers must then keep falling back to the
    model's proposals, because an old file genuinely cannot say which images
    were looked at, and silently hiding unreviewed hazards is the worse error.
    """
    labels = _load_json(run_dir / "labels.json")
    if not labels:
        return {}, None
    confirmed: dict[str, list[dict]] = {}
    for rec in labels.get("labels", []):
        if isinstance(rec, dict) and rec.get("confirmed") is not False and "xyxy" in rec:
            confirmed.setdefault(str(rec.get("image")), []).append(
                {"xyxy": rec["xyxy"],
                 "class": rec.get("class") or rec.get("proposed_class") or "Dead Tree"})
    covered = labels.get("reviewed_images")
    return confirmed, (set(map(str, covered)) if isinstance(covered, list) else None)


def boxes_for(image_path: str, proposals: list[dict],
              confirmed: dict[str, list[dict]], covered: Optional[set[str]]) -> list[dict]:
    """What this image actually shows: the reviewer's verdict when there is one,
    the model's proposals when there is not. An emptied image returns []."""
    verdict = confirmed.get(str(image_path))
    if verdict is not None:
        return verdict
    if covered is not None and str(image_path) in covered:
        return []  # reviewed and cleared - not "unreviewed"
    return proposals


def _rel_url(path_str: Optional[str], output_root: Path,
             run_dir: Optional[Path] = None) -> Optional[str]:
    """Stored artifact path -> /outputs/... URL (None if outside the root).

    `run_dir` resolves what batch.json holds: paths are stored relative to their
    run folder, and older runs hold absolutes that may point where the project
    used to live. Without it a moved project serves None for every image.
    """
    if not path_str:
        return None
    if run_dir is not None:
        path_str = run_absolute(path_str, run_dir)
    try:
        rel = Path(path_str).resolve().relative_to(output_root.resolve())
    except (ValueError, OSError):
        return None
    return "/outputs/" + rel.as_posix()


def scan_summary(run_dir: Path, settings: Settings) -> Optional[dict]:
    """Summarize one run folder, or None if it holds no usable run data.

    Confirmed (human-reviewed) counts take precedence over raw proposals:
    labels.json is the reviewer's verdict, batch.json the model's.
    """
    output_root = settings.output_path
    batch = _load_json(run_dir / "batch.json")
    labels = _load_json(run_dir / "labels.json")
    if batch is None and labels is None:
        return None

    reviewed = labels is not None
    if reviewed:
        counts = _counts_from_labels(labels)
    else:
        counts = dict((batch.get("stats") or {}).get("detections_by_type") or {})

    images = (batch or {}).get("images") or []
    gps = next((im.get("gps") for im in images if im.get("gps")), None)
    ts = _parse_run_timestamp(run_dir.name)
    if ts is None:
        gen = (batch or {}).get("batch_info", {}).get("generated_at")
        try:
            ts = datetime.strptime(gen, "%Y-%m-%d %H:%M:%S") if gen else None
        except ValueError:
            ts = None
    if ts is None:  # last resort: folder mtime keeps the run listable
        ts = datetime.fromtimestamp(run_dir.stat().st_mtime)

    image_count = len(images) or (batch or {}).get("batch_info", {}).get("image_count", 0)
    if not image_count and labels:
        image_count = len({r.get("image") for r in labels.get("labels", []) if isinstance(r, dict)})

    from ..report import latest_report

    report = latest_report(run_dir)
    # Reviewed runs preview the human-confirmed imagery, not the raw proposals.
    previews: list[str] = []
    if reviewed:
        previews = [u for u in (_rel_url(str(p), output_root)
                                for p in sorted((run_dir / "annotated").glob("*_confirmed.jpg"))[:4]) if u]
    if not previews:
        previews = [u for u in (_rel_url(im.get("annotated_path"), output_root, run_dir)
                                for im in images[:4]) if u]

    mix, flame_images = _severity_mix(images, settings)
    return {
        "id": run_dir.name,
        "created": ts.isoformat(timespec="seconds"),
        "date": ts.strftime("%b %d, %Y"),
        "time": ts.strftime("%H:%M"),
        "images": image_count,
        "detections_by_type": counts,
        "total_detections": sum(counts.values()),
        # The WORST frame in the run, not a verdict on the run. `severity_mix`
        # carries the rest; see _severity_mix for why both are needed.
        "severity": display_severity(counts, image_count,
                                     settings.severity_deadtrees_high,
                                     settings.severity_deadtrees_medium),
        "severity_mix": mix,
        "flame_images": flame_images,
        "reviewed": reviewed,
        "has_report": report is not None,
        "report_url": _rel_url(str(report), output_root) if report else None,
        "preview_urls": previews,
        "gps": list(gps) if gps else None,
        "status": "processed",
    }


def discover_scans(settings: Settings) -> list[dict]:
    """All completed runs under outputs/, newest first."""
    root = settings.output_path
    if not root.is_dir():
        return []
    scans = []
    for run_dir in root.iterdir():
        if not run_dir.is_dir() or run_dir.name.startswith(("_", ".")):
            continue
        s = scan_summary(run_dir, settings)
        if s:
            scans.append(s)
    scans.sort(key=lambda s: s["created"], reverse=True)
    return scans


def scan_detail(run_id: str, settings: Settings) -> Optional[dict]:
    """Full detail for one run: per-image detections + artifact URLs."""
    root = settings.output_path
    run_dir = (root / run_id)
    if not run_dir.is_dir() or ".." in run_id or "/" in run_id or "\\" in run_id:
        return None
    summary = scan_summary(run_dir, settings)
    if summary is None:
        return None

    batch = _load_json(run_dir / "batch.json") or {}
    confirmed_by_path, reviewed_paths = review_verdict(run_dir)

    # Per-image "the operator eyeballed this one" flags — an explicit user
    # action, distinct from having saved any boxes.
    reviewed_names = set((_load_json(run_dir / "reviewed_images.json") or {}).get("reviewed", []))

    scores = []
    images = []
    for im in batch.get("images") or []:
        dets = im.get("detections") or []
        # Confidence is a property of the model, so it is read from the raw
        # proposals; everything the operator SEES follows the verdict.
        scores.extend(float(d.get("score", 0)) for d in dets)
        shown = boxes_for(im.get("path"), dets, confirmed_by_path, reviewed_paths)
        stem = Path(str(im.get("path", ""))).stem
        conf_annot = run_dir / "annotated" / f"{stem}_confirmed.jpg"
        conf_grid = run_dir / "gridmaps" / f"{stem}_confirmed.jpg"
        images.append({
            "name": im.get("name"),
            "width": im.get("width"),
            "height": im.get("height"),
            "gps": im.get("gps"),
            "altitude": im.get("altitude"),
            "camera": im.get("camera"),
            "timestamp": im.get("timestamp"),
            "detections": dets,
            # None = never reviewed (show proposals); [] = reviewed and cleared.
            "confirmed": (confirmed_by_path.get(str(im.get("path")))
                          if confirmed_by_path.get(str(im.get("path"))) is not None
                          else ([] if reviewed_paths is not None
                                and str(im.get("path")) in reviewed_paths else None)),
            "reviewed_by_user": im.get("name") in reviewed_names,  # explicit per-image check
            "counts": _count_types(shown),  # thumbnail colour reads this
            "original_url": _rel_url(im.get("orig_display_path"), root, run_dir),
            "annotated_url": _rel_url(im.get("annotated_path"), root, run_dir),
            "gridmap_url": _rel_url(im.get("density_path"), root, run_dir),
            "confirmed_annotated_url": _rel_url(str(conf_annot), root) if conf_annot.exists() else None,
            "confirmed_gridmap_url": _rel_url(str(conf_grid), root) if conf_grid.exists() else None,
            "error": im.get("error"),
        })

    # A run imported from the cloud carries findings but no imagery, so the page
    # must say why the viewer is empty instead of showing broken frames.
    imported = _load_json(run_dir / ".cloud_import.json")
    batch_images = batch.get("images") or []
    summary.update({
        "batch_info": batch.get("batch_info") or {},
        "stats": batch.get("stats") or {},
        # The aircraft that shot the flight (EXIF), NOT the computer that processed it.
        "drone": next((im.get("camera") for im in batch_images if im.get("camera")), None),
        "images_detail": images,
        "reviewed_image_count": sum(1 for i in images if i["reviewed_by_user"]),
        "avg_confidence": round(sum(scores) / len(scores), 3) if scores else None,
        "peak_confidence": round(max(scores), 3) if scores else None,
        "results_only": bool(imported),
        "synced_from": (imported or {}).get("source") or None,
        "imported_at": (imported or {}).get("imported_at"),
        # Sent so the page can grade a single frame with the same ladder the
        # server used, instead of hardcoding numbers that drift from Settings.
        "severity_thresholds": {"high": settings.severity_deadtrees_high,
                                "medium": settings.severity_deadtrees_medium},
    })
    return summary


def _count_types(dets: list[dict]) -> dict:
    """Counts by hazard type, across both box shapes: a model proposal carries
    `display`, a reviewer's confirmed box carries `class`. Reading only one of
    them silently counted the other kind as "?"."""
    counts: dict[str, int] = {}
    for d in dets:
        tag = d.get("display") or d.get("class") or "?"
        counts[tag] = counts.get(tag, 0) + 1
    return counts


# ------------------------------------------------------------------ models
def _importable(module: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(module) is not None
    except Exception:
        return False


def model_status(settings: Settings) -> list[dict]:
    """Reality-checked status of every configured model source."""
    out = []
    for src in settings.model_sources:
        entry = {"key": src.key, "label": src.label or src.key,
                 "backend": src.backend, "enabled": src.enabled}
        if src.backend == "yolo":
            present = settings.model_path_for(src.filename).exists()
            entry["ready"] = src.enabled and present
            entry["note"] = src.filename if present else f"{src.filename} not downloaded yet"
        elif src.backend == "deepforest":
            # find_spec instead of importing: deepforest takes ~10s to import and
            # this endpoint must stay snappy.
            ok = _importable("deepforest")
            entry["ready"] = src.enabled and ok
            entry["note"] = "package installed" if ok else "deepforest not installed"
        elif src.backend == "onnx":
            present = settings.model_path_for(src.filename).exists()
            rt = _importable("onnxruntime")
            entry["ready"] = src.enabled and present and rt
            entry["note"] = (src.filename if present
                             else f"awaiting {src.filename} (train & export on Custom Vision)")
            if present and not rt:
                entry["note"] = "onnxruntime not installed"
        else:
            entry["ready"] = False
            entry["note"] = f"unknown backend '{src.backend}'"
        out.append(entry)
    return out


def detected_paths(settings: Settings) -> set[str]:
    """Source-image paths that already went through a detection run — used to
    split mission-folder flights into analyzed vs pending."""
    root = settings.output_path
    seen: set[str] = set()
    if not root.is_dir():
        return seen
    for run_dir in root.iterdir():
        if not run_dir.is_dir() or run_dir.name.startswith(("_", ".")):
            continue
        batch = _load_json(run_dir / "batch.json")
        for im in (batch or {}).get("images") or []:
            if im.get("path"):
                seen.add(str(im["path"]))
    return seen


# ------------------------------------------------------------------ map sites
def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


KIND_ORDER = {"flame": 3, "smoke": 2, "deadtree": 1}

_M_PER_DEG_LAT = 111320.0


def _cell_size_deg(points: list[dict], radius_m: float) -> tuple[float, float]:
    """Grid cell size in degrees, never narrower than `radius_m` in either axis.

    Sized off the highest latitude present, where a degree of longitude is
    shortest; cos is clamped so a bogus near-polar point cannot collapse the grid.
    """
    max_lat = max((abs(p["lat"]) for p in points), default=0.0)
    cos_lat = max(math.cos(math.radians(min(max_lat, 89.0))), 0.01)
    return radius_m / _M_PER_DEG_LAT, radius_m / (_M_PER_DEG_LAT * cos_lat)


def cluster_sites(points: list[dict], radius_m: float = 40.0) -> list[dict]:
    """Tier-1 dedup: greedy-cluster image points within `radius_m` into sites.

    Overlapping photos shoot the same trees, so one location appears in 3-5
    images; counting SITES is the honest number for "distinct locations flagged".

    Grid-indexed so each point only checks the 3x3 cells that could match. The
    plain scan it replaced took 7.5 s for a 10k-photo month, inside a web request.
    Output is identical, ties still going to the earliest site (test pins this).
    """
    if not points:
        return []
    rank = dict(SEVERITY_ORDER)
    sites: list[dict] = []
    lat_size, lon_size = _cell_size_deg(points, radius_m)
    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    site_cell: list[tuple[int, int]] = []  # where each site is currently indexed

    def cell(lat: float, lon: float) -> tuple[int, int]:
        return (math.floor(lat / lat_size), math.floor(lon / lon_size))

    for p in points:
        plat, plon = p["lat"], p["lon"]
        cx, cy = cell(plat, plon)
        best = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for idx in buckets.get((cx + dx, cy + dy), ()):
                    if best is not None and idx > best:
                        continue  # a lower index already wins; skip the distance
                    s = sites[idx]
                    if _haversine_m(plat, plon, s["lat"], s["lon"]) <= radius_m:
                        best = idx
        if best is None:
            sites.append({"lat": plat, "lon": plon, "severity": p["severity"],
                          "kind": p.get("kind", "deadtree"), "count": 0, "members": []})
            best = len(sites) - 1
            start = cell(plat, plon)
            buckets[start].append(best)
            site_cell.append(start)
        target = sites[best]
        n = target["count"]
        target["lat"] = (target["lat"] * n + plat) / (n + 1)  # running centroid
        target["lon"] = (target["lon"] * n + plon) / (n + 1)
        target["count"] = n + 1
        if rank.get(p["severity"], 0) > rank.get(target["severity"], 0):
            target["severity"] = p["severity"]
        if KIND_ORDER.get(p.get("kind"), 0) > KIND_ORDER.get(target["kind"], 0):
            target["kind"] = p["kind"]  # most critical hazard type wins the marker color
        target["members"].append({k: p[k] for k in ("run_id", "name", "severity", "thumb")
                                  if k in p})
        # The centroid just moved; re-index if it drifted into another cell,
        # or later points would look in the wrong place for it.
        moved = cell(target["lat"], target["lon"])
        if moved != site_cell[best]:
            buckets[site_cell[best]].remove(best)
            buckets[moved].append(best)
            site_cell[best] = moved
    return sites


def _point_month(im: dict, run: dict) -> str:
    """Capture month 'YYYY-MM' — EXIF timestamp first (when it was FLOWN, not
    when it was analyzed), falling back to the run date."""
    ts = str(im.get("timestamp") or "")  # EXIF 'YYYY:MM:DD HH:MM:SS'
    if len(ts) >= 7 and ts[:4].isdigit():
        return ts[:7].replace(":", "-")
    return str(run.get("created", ""))[:7]


def map_data(settings: Settings, radius_m: float = 40.0, month: str = "all") -> dict:
    """Per-image hazard points across all runs, clustered into dedup sites.

    `month` filters by capture time: 'all', 'latest', 'YYYY' or 'YYYY-MM' —
    hazards age (last month's dead trees may be cleared), so the map defaults
    to one period instead of mixing everything.
    """
    root = settings.output_path
    points: list[dict] = []
    for run in discover_scans(settings):
        run_dir = root / run["id"]
        batch = _load_json(run_dir / "batch.json")
        if not batch:
            continue
        # The map used to read batch.json alone, so a reviewer could delete a
        # false flame, save, and still find its pin on the map afterwards - and
        # the pin drives the hazard-type filter, so the whole map lied.
        confirmed, reviewed_paths = review_verdict(run_dir)
        for im in batch.get("images") or []:
            dets = boxes_for(im.get("path"), im.get("detections") or [],
                             confirmed, reviewed_paths)
            if not dets or not im.get("gps"):
                continue
            counts = _count_types(dets)
            sev = display_severity(counts, 1, settings.severity_deadtrees_high,
                                   settings.severity_deadtrees_medium)
            kind = ("flame" if counts.get("Flame") else
                    "smoke" if counts.get("Smoke") else "deadtree")
            points.append({"lat": float(im["gps"][0]), "lon": float(im["gps"][1]),
                           "severity": sev or "low", "kind": kind, "run_id": run["id"],
                           "name": im.get("name"), "month": _point_month(im, run),
                           "thumb": _rel_url(im.get("annotated_path"), root,
                                             root / run["id"])})

    month_counts: dict[str, int] = {}
    for p in points:
        if p["month"]:
            month_counts[p["month"]] = month_counts.get(p["month"], 0) + 1
    months = [{"key": k, "points": v} for k, v in sorted(month_counts.items(), reverse=True)]

    selected = month or "all"
    if selected == "latest":
        selected = months[0]["key"] if months else "all"
    if selected != "all":
        points = [p for p in points if p["month"].startswith(selected)]

    sites = cluster_sites(points, radius_m)
    return {"points": len(points), "sites": sites, "radius_m": radius_m,
            "months": months, "month": selected}


# ------------------------------------------------------------------ dashboard
def dashboard_summary(settings: Settings) -> dict:
    """Everything the dashboard page needs in one payload."""
    scans = discover_scans(settings)
    sev_counts = {"high": 0, "medium": 0, "low": 0}
    flagged = 0
    type_totals: dict[str, int] = {}
    reviewed_runs = pending_review = training_boxes = 0
    for s in scans:
        if s["severity"]:
            sev_counts[s["severity"]] += 1
        if s["total_detections"]:
            flagged += 1
        for k, v in s["detections_by_type"].items():
            type_totals[k] = type_totals.get(k, 0) + v
        if s["reviewed"]:
            reviewed_runs += 1
            training_boxes += s["total_detections"]  # reviewed counts = confirmed boxes
        elif s["total_detections"]:
            pending_review += 1

    pins = []
    for s in scans:
        if s["gps"] and s["severity"]:
            pins.append({"id": s["id"], "lat": s["gps"][0], "lon": s["gps"][1],
                         "severity": s["severity"], "date": s["date"], "time": s["time"],
                         "images": s["images"], "total_detections": s["total_detections"],
                         "thumb": (s["preview_urls"] or [None])[0]})

    last = scans[0] if scans else None
    return {
        "total_scans": len(scans),
        "high_risk": sev_counts["high"],
        "flagged_scans": flagged,
        "last_scan": {"date": last["date"], "time": last["time"], "id": last["id"]} if last else None,
        "severity_counts": sev_counts,
        "type_totals": type_totals,
        "review": {"reviewed": reviewed_runs, "pending": pending_review},
        "training_boxes": training_boxes,
        "recent": scans[:12],
        "pins": pins,
    }
