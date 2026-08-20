"""Gradio local web UI — human review/annotation stage (Layer 1.5).

Pipeline: Layer 1 (detect) -> **Layer 1.5 human review/annotation** -> Layer 2 (PDF).
Detections are PROPOSALS, not verdicts (RGB detection can't be trusted autonomously).
In the review the human DRAWS boxes for missed hazards, DELETES false proposals, and
sets each label. Only the confirmed boxes enter the report, and they are saved as a
Phase-2 training label set.

Run:  python -m src.wildfire.app    (opens http://127.0.0.1:7860)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import gradio as gr
from gradio_image_annotation import image_annotator

from .config import load_settings
from .detectors import build_detectors
from .llm import generate_analysis, health_check, resolve_model_id
from .models import ensure_yolo_sources
from .pipeline import run_batch
from .report import build_report, build_summary_text, timestamped_report_path
from .review import (
    REVIEW_COLORS, REVIEW_LABELS, build_confirmed_from_annotations,
    save_review_labels, to_annotator,
)

_DETECTORS = None  # built lazily on first detection (keeps startup instant)


def _get_detectors(settings):
    global _DETECTORS
    if _DETECTORS is None:
        _DETECTORS = build_detectors(settings, log=lambda m: print(m))
    return _DETECTORS


def _ann_value(ann_state, idx):
    st = ann_state.get(idx) if ann_state else None
    if not st:
        return None
    return {"image": st["image"], "boxes": st["boxes"]}


# ----------------------------------------------------------------- handlers
def run_detection(files, progress=gr.Progress()):
    settings = load_settings()
    if not files:
        raise gr.Error("Please upload one or more drone images first.")
    paths = [f if isinstance(f, str) else f.name for f in files]

    progress(0, desc="Loading models (first run downloads them)...")
    detectors = _get_detectors(settings)

    def cb(cur, tot, name):
        progress((cur, tot), desc=f"Detecting {cur}/{tot}: {name}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = settings.output_path / f"review_{ts}"  # one folder per run, no overwrite
    batch = run_batch(paths, detectors, settings, progress=cb,
                      out_dir=run_dir, batch_label=f"review_{ts}")
    # Persist raw proposals so the operations console can list this run.
    (run_dir / "batch.json").write_text(json.dumps(batch.to_dict(run_dir), indent=2), encoding="utf-8")

    progress(0.95, desc="Preparing review...")
    ann: dict = {}
    for i, im in enumerate(batch.images):
        try:
            disp, boxes, scale = to_annotator(im)
            ann[i] = {"boxes": boxes, "scale": scale, "image": disp, "name": im.name}
        except Exception:
            continue
    if not ann:
        raise gr.Error("No readable images to review.")

    choices = [(ann[i]["name"], i) for i in ann]
    cur = choices[0][1]
    s = batch.stats
    status = (f"{s['total_detections']} candidate detection(s) {s['detections_by_type']} "
              f"across {s['images_processed']} image(s). Draw boxes for missed hazards, "
              f"delete false ones, set labels — then generate the PDF.")
    return (status, gr.update(choices=choices, value=cur), _ann_value(ann, cur), batch, ann, cur)


def switch_image(new_idx, ann_value, cur, ann):
    if ann and cur in ann and ann_value:  # save edits to the image we are leaving
        ann[cur]["boxes"] = ann_value.get("boxes", []) if isinstance(ann_value, dict) else []
    return _ann_value(ann, new_idx), new_idx, ann


def generate_report_ui(ann_value, cur, ann, batch, progress=gr.Progress()):
    if batch is None or not getattr(batch, "images", None):
        raise gr.Error("Run detection first.")
    if ann and cur in ann and ann_value:  # capture the currently-open image's edits
        ann[cur]["boxes"] = ann_value.get("boxes", []) if isinstance(ann_value, dict) else []

    settings = load_settings()
    out_dir = Path(batch.batch_info.get("output_dir") or settings.output_path)  # this run's folder

    progress(0.3, desc="Rebuilding from confirmed boxes...")
    confirmed = build_confirmed_from_annotations(batch, ann, out_dir)
    save_review_labels(confirmed, out_dir / "labels.json")

    progress(0.6, desc="Requesting AI analysis from LM Studio...")
    ai_text = None
    model, _ = resolve_model_id(settings.lmstudio_url, settings.lmstudio_model)
    if model:
        ai_text, _ = generate_analysis(build_summary_text(confirmed), settings.lmstudio_url, model)

    progress(0.85, desc="Writing PDF...")
    from .config import PROJECT_ROOT
    pdf = build_report(confirmed, timestamped_report_path(out_dir), ai_text=ai_text,
                       max_image_pages=settings.report_max_image_pages,
                       detail_pages=settings.report_detail_pages,
                       map_dir=settings._resolve(settings.map_tiles_dir),
                       branding_dir=PROJECT_ROOT / "branding")
    n = confirmed.stats["total_detections"]
    note = "" if model else "  (LM Studio offline — AI analysis omitted.)"
    return str(pdf), (f"Report built with {n} confirmed detection(s). "
                      f"Labels saved to {out_dir / 'labels.json'}.{note}")


def check_lmstudio(url):
    up, ids, err = health_check(url)
    return f"Connected. Models: {ids}" if up else f"Not reachable: {err}"


def download_models():
    settings = load_settings()
    logs: list[str] = []
    paths = ensure_yolo_sources(settings, log=logs.append)
    try:
        from .deepforest_detector import deepforest_available
        df = "installed" if deepforest_available() else "NOT installed (pip install -r requirements-deadtree.txt)"
    except Exception:
        df = "unknown"
    return "\n".join(logs + [f"YOLO models: {[p.name for p in paths]}", f"DeepForest (dead-tree): {df}"])


# ----------------------------------------------------------------- UI
def build_ui():
    with gr.Blocks(title="Alberta Wildfire — Hazard Review", analytics_enabled=False) as demo:
        gr.Markdown("# 🌲🔥 Wildfire Hazardous Tree Mapping — Review\n"
                    "Detections are **proposals**. **Draw** missed hazards, **delete** false ones, "
                    "set each **label**; only confirmed boxes go into the PDF.")
        batch_st, ann_st, cur_st = gr.State(), gr.State(), gr.State()

        with gr.Tabs():
            with gr.Tab("Detect & Review"):
                with gr.Row():
                    files = gr.File(label="Drone images (JPG/TIFF)", file_count="multiple",
                                    file_types=["image"], type="filepath")
                    with gr.Column():
                        run_btn = gr.Button("1) Run detection", variant="primary")
                        status = gr.Textbox(label="Status", interactive=False, lines=3)
                img_pick = gr.Dropdown(label="2) Image to review", choices=[], interactive=True)
                gr.Markdown("Draw a box by dragging on the image. **Delete**: click a box to "
                            "select it, then the 🗑 button (or press Delete). Set the label per box.")
                annotator = image_annotator(
                    label="Review — draw / edit / delete boxes, set labels",
                    label_list=REVIEW_LABELS,
                    label_colors=[REVIEW_COLORS[k] for k in REVIEW_LABELS],
                    show_remove_button=True,
                    show_clear_button=True,
                    use_default_label=True,
                )
                with gr.Row():
                    gen_btn = gr.Button("3) Generate PDF (confirmed only)", variant="primary")
                report_file = gr.File(label="PDF report")
                report_status = gr.Textbox(label="Report", interactive=False)

            with gr.Tab("Settings"):
                lm_url = gr.Textbox(label="LM Studio URL", value="http://localhost:1234")
                lm_btn = gr.Button("Check LM Studio connection")
                lm_status = gr.Textbox(label="Connection", interactive=False)
                dl_btn = gr.Button("Download / check models")
                dl_status = gr.Textbox(label="Models", interactive=False, lines=4)

        run_btn.click(run_detection, inputs=[files],
                      outputs=[status, img_pick, annotator, batch_st, ann_st, cur_st])
        img_pick.change(switch_image, inputs=[img_pick, annotator, cur_st, ann_st],
                        outputs=[annotator, cur_st, ann_st])
        gen_btn.click(generate_report_ui, inputs=[annotator, cur_st, ann_st, batch_st],
                      outputs=[report_file, report_status])
        lm_btn.click(check_lmstudio, inputs=[lm_url], outputs=[lm_status])
        dl_btn.click(download_models, outputs=[dl_status])
    return demo


def main() -> None:
    # WILDFIRE_NO_BROWSER=1 is set when the operations console spawns this app —
    # the console opens its own tab, so a second one here would be a duplicate.
    inbrowser = os.environ.get("WILDFIRE_NO_BROWSER") != "1"
    build_ui().launch(server_name="127.0.0.1", server_port=7860, inbrowser=inbrowser, share=False)


if __name__ == "__main__":
    main()
