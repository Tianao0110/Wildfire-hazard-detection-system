"""CLI: run the Layer 1 detection pipeline on a folder of images.

This is the headless acceptance harness for Layer 1 — it runs the full pipeline
(models -> SAHI+YOLO + DeepForest -> annotate + grid map -> GPS -> flag/stats) and
writes annotated/grid-map JPGs plus a batch.json, with no UI involved.

Usage (from the project root):
    python -m scripts.run_detection sample_data
    python -m scripts.run_detection sample_data --out outputs --conf 0.25 --slice 1024
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Allow running as a plain script (not just `python -m`).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Some deps (Lightning/rich) emit Unicode; avoid crashes on non-UTF-8 consoles.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from src.wildfire.config import load_settings  # noqa: E402
from src.wildfire.detectors import build_detectors  # noqa: E402
from src.wildfire.device import device_label  # noqa: E402
from src.wildfire.imageio_utils import list_images  # noqa: E402
from src.wildfire.pipeline import run_batch  # noqa: E402


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Run Layer 1 wildfire detection on a folder.")
    ap.add_argument("folder", help="Folder of drone images (JPG/TIFF).")
    ap.add_argument("--out", default=None, help="Output folder (default: settings.output_dir).")
    ap.add_argument("--conf", type=float, default=None, help="Confidence threshold override.")
    ap.add_argument("--slice", type=int, default=None, help="SAHI slice size override.")
    ap.add_argument("--pdf", action="store_true", help="Also build a PDF report (Layer 2).")
    return ap.parse_args()


def main() -> int:
    args = _parse_args()
    settings = load_settings()
    if args.conf is not None:
        settings.conf_threshold = args.conf
    if args.slice is not None:
        settings.slice_size = args.slice

    images = list_images(args.folder)
    if not images:
        print(f"No supported images (JPG/TIFF) found in {args.folder!r}.", file=sys.stderr)
        return 2

    print(f"Device: {device_label()}")
    print("Building detectors (this downloads/loads models on first run)...")
    detectors = build_detectors(settings, log=lambda m: print(f"  {m}"))
    print(f"Detectors: {[d.name for d in detectors]}")

    # Per-run subfolder by default so results don't overwrite/mix.
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out) if args.out else settings.output_path / f"{Path(args.folder).name}_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Processing {len(images)} image(s) -> {out_dir}\n")

    try:
        from tqdm import tqdm

        bar = tqdm(total=len(images), unit="img")

        def progress(cur: int, total: int, name: str) -> None:
            bar.update(1)
            bar.set_postfix_str(name)
    except Exception:
        bar = None

        def progress(cur: int, total: int, name: str) -> None:
            print(f"  [{cur}/{total}] {name}")

    batch = run_batch(images, detectors, settings, progress=progress, out_dir=out_dir,
                      batch_label=Path(args.folder).name)
    if bar is not None:
        bar.close()

    json_path = out_dir / "batch.json"
    json_path.write_text(json.dumps(batch.to_dict(out_dir), indent=2), encoding="utf-8")

    s = batch.stats
    print("\n=== Batch summary ===")
    print(f"  Images processed : {s['images_processed']}")
    print(f"  Flagged images   : {s['flagged_images']}")
    print(f"  Total detections : {s['total_detections']} {s['detections_by_type']}")
    print(f"  With dead tree   : {s['images_with_deadtree']}")
    print(f"  With flame/smoke : {s['images_with_flame']} / {s['images_with_smoke']}")
    print(f"  With GPS         : {s['images_with_gps']}")
    errors = [im.name for im in batch.images if im.error]
    if errors:
        print(f"  Errors           : {len(errors)} -> {errors}")
    print(f"\nWrote {json_path}")
    print(f"Annotated + grid-map JPGs in {out_dir}")

    if args.pdf:
        from src.wildfire.llm import generate_analysis, resolve_model_id
        from src.wildfire.report import build_report, build_summary_text, timestamped_report_path

        print("\nBuilding PDF report...")
        ai_text = None
        model, err = resolve_model_id(settings.lmstudio_url, settings.lmstudio_model)
        if model:
            print(f"  LM Studio model: {model} - generating analysis...")
            ai_text, aerr = generate_analysis(build_summary_text(batch), settings.lmstudio_url, model)
            if ai_text is None:
                print(f"  LM Studio: {aerr}")
        else:
            print(f"  LM Studio: {err}")
        from src.wildfire.config import PROJECT_ROOT
        pdf = build_report(batch, timestamped_report_path(out_dir), ai_text=ai_text,
                           max_image_pages=settings.report_max_image_pages,
                           detail_pages=settings.report_detail_pages,
                           map_dir=settings._resolve(settings.map_tiles_dir),
                           branding_dir=PROJECT_ROOT / "branding")
        print(f"  Wrote {pdf} ({pdf.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
