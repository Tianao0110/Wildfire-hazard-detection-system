# Wildfire Hazardous Tree Mapping System

A fully-offline, AI-powered desktop tool that analyzes drone photos of forest to detect
**hazardous standing dead trees** (primary) plus **flame and smoke** (secondary), lets a
human reviewer confirm every detection, and produces PDF field reports with an offline
satellite hazard map.

> **Team members:** start with **[TEAM_GUIDE.md](TEAM_GUIDE.md)** — what the project
> is, how to run and maintain it, how to explain it, and (part 2) how to work on
> the code.
> **AI coding sessions:** read **[AGENTS.md](AGENTS.md)** first — project context,
> hard rules, commands, architecture, design decisions and known pitfalls.
> **End users:** see **[MANUAL.md](MANUAL.md)** for system requirements, installation,
> the standard field workflow, troubleshooting and known limits.
> Packaging/distribution: **[INSTALL.md](INSTALL.md)**. Delivery progress: **[PLAN.md](PLAN.md)**.

Built as a capstone project at SAIT (2026). It is an independent student project,
not a SAIT product, and does not carry the institution's branding. **No drone
imagery is included in this repository:** the flight photos used during development
were provided under the capstone agreement and are not redistributable. Point the
console at your own mission folder to try it.

## How it works

- **Layer 1 — Detection**: drone images → optional pre-compression
  (`preprocess_max_mb`, resolution kept) → SAHI slicing (5280×3956 frames) →
  YOLO11 flame/smoke + DeepForest dead-tree proposals → annotated images
  (dead tree = bone, flame = red, smoke = slate) + grid density maps + GPS
  (DJI RTK **.MRK** beside the photos preferred — cm-grade — falling back to EXIF).
  Detection runs in a **low-priority worker process**, so the UI never blocks.
  No formal risk classification — detections are flagged with their GPS location.
- **Layer 1.5 — Review** (the accuracy guarantee): detections are **proposals**, a human
  confirms them in the console's built-in box editor (zoom, draw, delete, relabel).
  Confirmed boxes become the run's `labels.json` — simultaneously the report source
  and the **training set** for the custom model.
- **Layer 2 — Report**: survey facts (flight metadata, densities, ranked hotspots) →
  LM Studio local LLM (graceful fallback when offline) → five-section professional
  analysis → ReportLab PDF, ordered so the conclusions come before the evidence:
  cover → batch summary → a full-page **real offline satellite hazard map** stitched
  from the tile cache → AI analysis → full detail pages for the worst
  `report_detail_pages` images → a gallery contact sheet (8 per page) for the rest
  of the top `report_max_image_pages` → an index row for **every** image.

> **Detection honesty:** RGB imagery physically cannot separate dead wood from bare
> brown ground reliably — that signal lives in SWIR bands RGB cameras don't capture
> (confirmed by literature + tests on real imagery). Hence review-first design; real
> autonomy comes from Phase-2 training on your confirmed labels.

Everything runs locally. Network is only needed for one-time setup downloads
(dependencies, model weights, map tiles) and the optional local LM Studio call.

## The operations console

The main interface — a FastAPI app at `http://127.0.0.1:7861`, installable as a
**desktop application** (native window, own icon, no terminal):

```bash
python -m scripts.install_desktop_app   # Windows: Desktop + Start Menu shortcuts
                                        # macOS:   ~/Applications/<app>.app
# alternatives: run-console-windows.bat / run-console-macos.command (browser mode)
#               python -m src.wildfire.console
```

| Page | What it does |
|------|--------------|
| **Dashboard** | Stat cards, hazard mini-map (legend chips filter pins by type), detections by type, review backlog, training-set size |
| **Scans** | Mission-folder browser + upload + model status + run history |
| **Scan Detail** | Zoomable viewer (wheel/drag/dbl-click), click-to-locate detections, **box review editor**, report generation |
| **Review** | All runs grouped by day with needs-review/reviewed status — the backlog view |
| **Map** | Offline satellite map (Leaflet) with month/year + hazard-type pin filters and ≤40 m **site dedup**; in-UI tile download |
| **Reports** | Every generated PDF, newest first (timestamped, never overwritten) |
| **Settings** | Detection params, severity thresholds, model toggles, ONNX preprocessing, LM Studio test, map download |

Key behaviors:

- **Mission folder**: point it at a flight folder / SD-card dump (persisted, reloaded on
  startup). Only images are read — telemetry sidecars are ignored. Images are grouped by
  EXIF capture time into **flights** (>45 min gap or day change; continuous shoots split
  into ~100-image parts), each with a picker to detect a chosen subset. Fully analyzed
  flights collapse into an *Analyzed* section.
- **Severity badge** (display-only, no risk field in data): Flame → High;
  ≥`severity_deadtrees_high` (10) avg dead trees/image → High; Smoke → Medium;
  ≥`severity_deadtrees_medium` (3) → Medium; else Low.
- **Offline map data**: Map → *⬇ Map data* downloads Esri satellite tiles for the scanned
  area / whole Alberta / a custom rectangle (Esri permits offline export; Google/Bing do
  not). CLI equivalents: `scripts/fetch_map_tiles.py`, `scripts/fetch_map_overlays.py`
  (OSM roads/rivers/lakes).
- The legacy Gradio annotator (`run-app-windows.bat` / `run-app-macos.command`, port 7860) still works but is no longer
  needed — review happens inside the console.

## Requirements & setup

Minimums (details in [MANUAL.md](MANUAL.md)): Windows 10/11 64-bit **or** macOS 11+,
Python 3.13, 8 GB RAM, ~15 GB disk. Any NVIDIA GPU enables CUDA; Apple Silicon uses
Metal (MPS) automatically. CPU works everywhere, just slower.

```powershell
# Windows
py -3.13 -m venv .venv
.venv\Scripts\activate
python -m pip install -U pip
pip install -r requirements-win-cuda.txt   # CUDA torch FIRST
pip install -r requirements.txt
pip install -r requirements-deadtree.txt   # DeepForest dead-tree proposer (heavy)
```

```bash
# macOS
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -r requirements-mac.txt        # CPU/MPS torch FIRST
pip install -r requirements.txt
pip install -r requirements-deadtree.txt
```

Or just double-click **`install-windows.bat`** / **`install-macos.command`** — either
does all of the above plus the desktop app. Every script in the project root names
its OS; run only your platform's, since the two pull different PyTorch builds.

## Headless CLI (optional)

```bash
python -m scripts.run_detection <folder> --pdf     # detect + report, no UI
python -m scripts.generate_report                  # PDF from an existing batch.json
```

## Run outputs

Each run gets its own folder; artifacts are sorted by kind so a 100-image flight stays
navigable:

```
outputs/<run>_<timestamp>/
  originals/<image>.jpg             full-res copy of each input
  annotated/<image>.jpg             detection boxes drawn
  annotated/<image>_confirmed.jpg   re-rendered from reviewer-confirmed boxes
  gridmaps/<image>.jpg              per-cell hazard-count map (+ _confirmed)
  batch.json                        per-image detections, GPS, statistics
  labels.json                       confirmed boxes — the training set
  report_<timestamp>.pdf            never overwritten
```

### Cloud sync (optional)

Everything above stays local by default — the app has no dependency on this. Settings →
**Cloud sync** shares a run through an **Azure Blob Storage** container so other machines
can see it.

**Results only (default).** One gzipped JSON per run — detections, GPS, statistics and the
confirmed labels. **Imagery never leaves the machine.** Measured on the real runs in this
repo: 2,056 MB on disk against **48 KB** uploaded, roughly 12 bytes per detection, so the
1,558-image May mission lands near 1 MB. PDFs are excluded (one was 55.8 MB on its own);
any machine can regenerate a report from the findings it received.

A run pulled down this way is flagged **results-only**: the map, counts, dashboard and
Image Index all work, the photo viewer says the imagery stayed behind, and box review is
hidden because there is nothing to draw on. Reports built from it skip the per-image pages
and go cover → summary → hazard map → AI analysis → index.

**Everything (opt-in).** Uploads the whole run folder — originals, annotated frames, grid
maps and the PDF — as an off-site backup. Same 58-image flight: ~1.7 GB. Incremental, so
re-syncing after a review pass only sends what changed.

- **Manual**: a "☁ Sync to cloud" button on each run's Scan Detail page.
- **Automatic**: enable "Auto-upload" and a run syncs itself right after its report finishes.
- **Import**: Scans page → *Cloud* card lists what is in the container; importing writes a
  local results-only run. A run this machine already holds is never silently replaced.
- **Ownership, not conflict resolution**: a run belongs to the machine that produced it
  (`source.machine` in the payload). Others import it read-only. That is the right trade
  for a single-operator tool and it is why there is no merge logic here.
- Requires `pip install azure-storage-blob` (already in `requirements.txt`). The package is
  imported lazily, so an install without it runs normally with cloud sync switched off.

**There is no login and no account system.** Access is whatever the credential allows, so
the credential *is* the boundary — pick a narrow one.

*Admin, once:* create a Storage Account and a **private** container (`wildfire-runs`), then
open the container → *Shared access tokens* → permissions **Read, Write, List** (leave
**Delete** off) → set an expiry → copy the generated **Blob SAS URL**.

*Each machine, once:* Settings → Cloud sync → paste that URL → *Test connection* → enable.
No username, no password. On shared machines set `AZURE_STORAGE_CONNECTION_STRING` instead
and the secret never touches `config/settings.json`.

A full connection string containing `AccountKey=` is also accepted, and *Test connection*
will say so — that key grants read/write/**delete** across the whole storage account and
never expires, so it is the wrong thing to copy onto field laptops. A scoped SAS reaches
one container, can be issued per device, expires on its own, and can be revoked
individually. Note a scoped token cannot create its container: make it in the portal first.

## Detection models

Detector backends are a small registry in `src/wildfire/detectors.py` — a new model is
one class + one builder + a `model_sources` entry in `config/settings.json`:

- **Dead trees (primary, `backend: "deepforest"`):** [DeepForest](https://deepforest.readthedocs.io)
  crown detector + alive/dead classifier (weights auto-download once, cached offline).
  Zero-training Phase-1 proposer; Phase 2 replaces it with your custom model.
- **Flame & smoke (`backend: "yolo"`):** auto-downloads `firedetect-11s.pt`
  (`leeyunjai/yolo11-firedetect`), GitHub fallback; runs through SAHI slicing. Any extra
  YOLO `.pt` dropped into `models/` is also run.
- **Custom dead-tree model (Phase 2, `backend: "onnx"`):** drop your trained export into
  `models/` as `dead_tree.onnx` + `dead_tree.labels.txt` — picked up automatically.
  Handles Azure Custom Vision and Ultralytics ONNX layouts, manual tiling + NMS,
  preprocessing tunable via the `onnx_*` settings (also in the Settings page).

## Training your own model (Azure Custom Vision → ONNX)

Every saved review grows `labels.json`. The loop:

```bash
# 1) Export as Custom Vision-ready tiles + normalized regions (tiling matches inference)
python -m scripts.export_labels outputs/<run>

# 2) customvision.ai: Object Detection project, compact domain "General (compact) [S1]"
#    (compact = ONNX-exportable)

# 3) Upload with boxes (the one online step; pip install azure-cognitiveservices-vision-customvision)
python -m scripts.upload_to_custom_vision <export_dir> --endpoint <url> --key <key> --project-id <guid>

# 4) Train → Export → ONNX → copy into models/ as dead_tree.onnx + dead_tree.labels.txt
```

If your model returns empty/garbled boxes, flip `onnx_normalize` (`0-255` ↔ `0-1`) or
`onnx_channel_order` (`RGB` ↔ `BGR`) in Settings — no code changes.

## Tests

```bash
pip install -r requirements-dev.txt   # pytest + pypdf, once per machine
pytest -q     # 120 tests; heavy integration tests auto-skip without models
```

## Project layout

```
src/wildfire/    Layer 1: config, device, models, detect (SAHI/YOLO), deepforest_detector,
                 onnx_detector, detectors (backend registry), imageio_utils, gps (EXIF+MRK),
                 annotate, risk, pipeline, types
                 Layer 2: llm (LM Studio), report (PDF + offline satellite map)
                 Phase 2: cv_export (labels.json -> Custom Vision dataset)
                 Legacy UI: app (Gradio), review
src/wildfire/console/
                 server (FastAPI + JSON API), data (runs/severity/sites), ingest
                 (mission folder), jobs + worker (subprocess detection), tiles
                 (offline map download), desktop (native window), pages/ + static/
mobile/          Expo field viewer for phones - reads the results this system
                 uploads to Azure; runs no detection of its own (mobile/README.md)
scripts/         run_detection, generate_report, download_model, export_labels,
                 upload_to_custom_vision, fetch_map_tiles, fetch_map_overlays,
                 install_desktop_app
tests/           190 unit + integration tests
config/          settings.example.json (settings.json is created on first run)
models/          detector weights (gitignored)
map/             offline satellite tiles + overlays.geojson (gitignored)
outputs/         one folder per run (gitignored)
MANUAL.md        user manual · INSTALL.md packaging · PLAN.md delivery checklist
```
