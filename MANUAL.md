# Wildfire Hazard Detection System — User Manual

An offline desktop application that analyzes drone photos of forest, detects
**standing dead trees** (primary) and **flame / smoke** (secondary), lets a human
reviewer confirm every detection, and produces PDF field reports with an offline
satellite hazard map. After installation it runs **without any internet
connection** — designed for field laptops.

---

## 1. System requirements

The application runs on **Windows 10/11** and on **macOS 11 or newer**.

| | Minimum | Recommended |
|---|---|---|
| **OS** | Windows 10 64-bit (with [WebView2 runtime](https://developer.microsoft.com/microsoft-edge/webview2/)) · macOS 11 Big Sur | Windows 11 (WebView2 built in) · macOS 14+ on Apple Silicon |
| **CPU** | 4-core x86-64 · any Apple Silicon (M1+) or Intel Mac | 8-core or better |
| **RAM** | 8 GB | 16 GB (32 GB if running LM Studio for AI report text) |
| **GPU** | none — runs on CPU, slow | Windows: NVIDIA GPU, ≥ 4 GB VRAM, driver supporting CUDA 12.x · macOS: Apple Silicon, used automatically through Metal (MPS) |
| **Disk** | 15 GB free | 30 GB+ free, SSD |
| **Display** | 1280 × 800 | 1920 × 1080 |
| **Python** | 3.13 (installed by you; venv created by the installer) | — |
| **Internet** | install time only | also for model & map-tile downloads (one-time) |

Performance reference: one 5280 × 3956 drone photo takes **~13 s on an RTX-class
GPU** and **several minutes on CPU**. A 250-image flight ≈ 1 hour on GPU.
Analysis writes ~10–15 MB of imagery per photo into `outputs/` — budget disk
accordingly (a 250-image flight ≈ 3 GB; the output folder is configurable in
Settings).

There is no CUDA on a Mac. Apple Silicon uses the built-in GPU through Metal
(MPS) and needs no driver install; Intel Macs run on CPU, which works but is the
slow end of the table above.

## 2. Installation

The release archive contains the scripts for both platforms, and each filename
says which OS it is for. **Run only the one matching your computer** — the two
installers fetch different PyTorch builds, so the wrong one cannot work.

| | Windows | macOS |
|---|---|---|
| Install (once) | `install-windows.bat` | `install-macos.command` |
| Console in a browser | `run-console-windows.bat` | `run-console-macos.command` |
| Legacy review app | `run-app-windows.bat` | `run-app-macos.command` |

### Windows

1. Install **Python 3.13** from python.org — tick *“Add python.exe to PATH”*.
2. Unzip the release archive to a path **without spaces or non-ASCII characters**
   (e.g. `C:\Wildfire`).
3. Double-click **`install-windows.bat`** — creates the environment, installs PyTorch
   (CUDA build, automatic CPU fallback) and all dependencies, and puts a
   **“Wildfire Hazard Detection”** shortcut on the Desktop and Start Menu.
   One-time, needs internet, ~4 GB download.
4. Launch from the desktop shortcut — a native window opens, no terminal.

### macOS

1. Install **Python 3.13**: `brew install python@3.13` (or the python.org
   installer).
2. Unpack the release archive anywhere in your home folder.
3. Double-click **`install-macos.command`** — Finder opens Terminal and runs it.
   The same steps as above with the Apple Metal / CPU build of PyTorch, ending
   with a **“Wildfire Hazard Detection”** app in `~/Applications` plus a
   shortcut to it on the Desktop. One-time, needs internet, ~4 GB download.
4. Double-click **“Wildfire Hazard Detection”** on the Desktop — a native window
   opens, no terminal. Spotlight (⌘-Space) finds it by name too, and you can
   drag it to the Dock. *(macOS 26 removed Launchpad — use Spotlight.)*

Details and packaging instructions: see `INSTALL.md`.

## 3. First-time setup (once, while online)

1. **Detection models** — Settings → *Download missing models* (fire/smoke
   weights). The dead-tree DeepForest weights download automatically on the
   first detection run.
2. **Offline map** — Map → *⬇ Map data*: pick **Around my scanned area**
   (high-detail) and optionally **Alberta province base map**. Roads/rivers
   overlays: `python -m scripts.fetch_map_overlays --bbox <lat_min lon_min lat_max lon_max>`.
3. *(Optional)* **LM Studio** for AI-written report text: install LM Studio,
   load a Qwen-class model, start its server (localhost:1234), then verify with
   Settings → *Test connection*. Without it, reports simply omit the AI section.

After this, the machine can go fully offline.

## 4. Standard workflow

```
SD card in  →  Scans: Mission Folder →  flight card: Detect  →  Scan Detail:
Review boxes (confirm/draw/delete)  →  Generate Report (PDF)  →  Map / Reports
```

1. **Load the mission folder** — Scans page → paste the flight/SD-card path
   (e.g. `F:\DCIM`) → *Load folder*. Images are grouped by day and flight
   (a >45-min gap or a new day starts a new flight; huge continuous shoots are
   split into ~250-image parts). Telemetry sidecars are ignored automatically;
   DJI RTK `.MRK` files beside the photos are used for cm-grade GPS.
   Already-analyzed flights collapse into the **✓ ANALYZED** section.
2. **Detect** — *Detect all* on a flight card, or *Select images* to pick a
   subset in the full-screen chooser. Detection runs in a background
   low-priority process — keep using the app while it works; progress shows in
   the Recent Scans table.
3. **Review** (the human is the final classifier — detections are proposals):
   open the run → *✎ Review boxes* → wheel-zoom in, delete false boxes, draw
   missed hazards, set labels → *Save review*. Confirmed boxes become the
   training set (`labels.json`) and all counts/maps update.
4. **Report** — *Generate Report* on the detail page. Each PDF is timestamped,
   never overwritten; all PDFs are listed on the Reports page.
5. **Overview** — Dashboard (stats, map, review backlog, training-set size),
   Review page (day-by-day backlog), Map page (site markers: flame red, smoke
   slate grey, dead tree bone white, each with its own icon; nearby images
   within 40 m merge into one site). **Double-click a marker** to open the map
   fullscreen zoomed to that site — or use the *Fullscreen* button; *Esc* exits.
   On both maps the legend doubles as a filter: click a hazard type to hide or
   show its pins and its site-list rows (one shared choice, remembered on that
   computer).

## 5. Where your data lives

```
outputs/<run>_<timestamp>/     one folder per analysis run
  originals/ annotated/ gridmaps/   imagery (raw / boxed / density)
  batch.json                        detections + GPS (machine-readable)
  labels.json                       your confirmed boxes = training data
  report_<timestamp>.pdf            reports (never overwritten)
models/                        detector weights (.pt / your dead_tree.onnx)
map/                           offline satellite tiles + road/water overlays
config/settings.json           all settings (editable in the Settings page)
```

Everything is local; nothing is uploaded anywhere.

## 6. Training your own dead-tree model (Phase 2)

1. Review runs in the app — every *Save review* grows the training set.
2. `python -m scripts.export_labels outputs/<run>` → Custom Vision dataset.
3. Upload & train on customvision.ai (Object Detection, **compact [S1]** domain);
   see README “Training your own model”.
4. Export as ONNX → drop `model.onnx` as `models/dead_tree.onnx` and
   `labels.txt` as `models/dead_tree.labels.txt` → the app uses it on the next
   run. If boxes come out empty/garbled, flip *Settings → Custom ONNX model →
   Normalize / Channel order*.

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| Window opens blank / never loads | Windows: install the WebView2 runtime (Win 10), then relaunch. macOS: quit and reopen — the window is WebKit and needs no runtime |
| “Port 7861 is in use” | Another copy is already running — check the taskbar / Task Manager, or the Dock / Activity Monitor on macOS |
| macOS: nothing happens when you open the app | The venv moved or was rebuilt — re-run `install-macos.command`, which rewrites the launcher |
| macOS: cannot find the app after installing | It is on the Desktop, and in `~/Applications` — which is **not** the “Applications” in the Finder sidebar (that one is `/Applications`). ⌘-Space and type the name always works |
| macOS: the icon looks blank or generic | The icon cache is stale. Re-run `install-macos.command`; it re-registers the bundle and restarts the Dock |
| Detection stuck at “loading detection models…” | First run downloads DeepForest weights — needs internet once; check `outputs/<run>/_worker.log` |
| Every image says *no detections* with your ONNX model | Flip `onnx_normalize` to `0-1` or channel order to `BGR` in Settings |
| Map is a stylized placeholder | No tiles cached for this area — Map → *⬇ Map data* |
| Report has no AI analysis section | LM Studio isn’t running — optional; start it and regenerate |
| GPS missing on the map | Photos lack EXIF GPS and no `.MRK` file sits beside them |
| App feels slow during detection | Expected on CPU-only machines; the UI stays usable — check progress in Scans |
| Page shows errors or looks half-updated right after a software update | Should no longer happen — the console versions its own files since 2026-08-19. If it does, close and reopen the app; if that fails, hard-refresh (Ctrl+Shift+R; ⌘⇧R on macOS) and report it, because it now means a real bug |

## 8. Known limits (by design)

- Detections are **proposals**: RGB imagery physically cannot separate dead
  wood from bare ground reliably (the signal is in SWIR bands). The human
  review step is the accuracy guarantee, and it doubles as training-data
  collection for the custom model.
- The High/Medium/Low badge is a **display aid** derived from detection density
  and type (thresholds in Settings) — no formal risk score is stored.
- Overlapping flight photos re-shoot the same trees; the map merges images
  within 40 m into one *site*, but raw detection counts still contain overlap.
  Full de-duplication (orthomosaic pipeline) is on the roadmap.
