"""Settings: load/save JSON config with sane defaults and resolved paths.

`config/settings.json` is created from `config/settings.example.json` on first
load. Unknown keys in the JSON are ignored so the file can carry extra notes.

Detection uses one or more models (primary dead-tree + secondary fire/smoke),
each described by a ModelSource entry so the in-app download manager knows where
to fetch it. Detections from all available models are merged.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Optional

# config.py lives at src/wildfire/config.py -> project root is parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
EXAMPLE_PATH = CONFIG_DIR / "settings.example.json"
SETTINGS_PATH = CONFIG_DIR / "settings.json"


@dataclass
class ModelSource:
    """A downloadable detection model for the download manager.

    `hf_repo_id` + `hf_filename` is the primary (Hugging Face) source;
    `fallback_url` is a zero-auth direct download used if HF is unavailable.
    A source with no URLs is "bring your own": drop a .pt into models/ yourself.
    """

    key: str  # "deadtree" / "firesmoke" / "deadtree_onnx"
    filename: str  # local filename under models/ (for backend="yolo"/"onnx")
    label: str = ""  # human label for the UI
    backend: str = "yolo"  # "yolo" (SAHI+ultralytics .pt), "deepforest", or "onnx"
    hf_repo_id: str = ""
    hf_filename: str = ""
    fallback_url: str = ""
    labels_filename: str = ""  # backend="onnx": class-names file (Custom Vision labels.txt)
    enabled: bool = True
    # --- backend="yolo" only; the defaults reproduce the old behaviour ---
    # Ground resolution the model was trained at; each frame is resampled toward
    # it before inference (0 = run at native scale). See gps.ground_sample_distance_cm.
    target_gsd_cm: float = 0.0
    slice_size: int = 0        # SAHI tile edge for this model (0 = Settings.slice_size)
    merge_ios: float = 0.5     # GREEDYNMM intersection-over-smaller to merge tiles
    # raw model class -> display label; "" drops the class. Unlisted classes go
    # through models.display_for as before.
    class_map: dict = field(default_factory=dict)


# Which generation of the detection pipeline produced a run, recorded in every
# batch.json. BUMP IT when a change makes counts from older runs incomparable -
# the severity thresholds are calibrated per generation, so an old run graded on
# new thresholds reads as more (or less) severe than it is.
#   1 - (runs without the field) crown model at native scale: ~70 "dead trees"
#       per frame, median 0.9 m wide, no fallen trees
#   2 - 2026-11-05: crown model at its training scale (~20 per frame), fallen
#       trees from the deadwood model, flame/smoke confidence floors
DETECTOR_GENERATION = 2


def _default_model_sources() -> list[ModelSource]:
    return [
        ModelSource(
            key="deadtree",
            filename="dead_tree.pt",
            label="Hazardous dead trees (primary)",
            backend="deepforest",  # weecology DeepForest crown detector + alive/dead classifier
        ),
        ModelSource(
            key="firesmoke",
            filename="fire_smoke.pt",
            label="Flame & smoke (secondary)",
            hf_repo_id="leeyunjai/yolo11-firedetect",
            hf_filename="firedetect-11s.pt",
            fallback_url=(
                "https://raw.githubusercontent.com/sayedgamal99/"
                "Real-Time-Smoke-Fire-Detection-YOLO11/main/models/best_nano_111.pt"
            ),
        ),
        # Fallen trees: instance segmentation of downed and standing deadwood from
        # RGB UAV imagery, Mayra et al. 2025 (Remote Sensing of Environment 329,
        # 114906), trained in Finnish boreal forest at ~4-5 cm/px on 640 px chips.
        # Only its fallen class is used: the crown model above already covers
        # standing dead trees, and this one found ~2 per frame that would double
        # count them. AGPL-3.0, the same licence Ultralytics itself carries.
        ModelSource(
            key="deadwood",
            filename="deadwood_yolov8s.pt",
            label="Fallen trees (YOLOv8 deadwood, Mayra et al. 2025)",
            backend="yolo",
            hf_repo_id="mayrajeo/yolov8-deadwood",
            hf_filename="models/YOLOv8s_both.pt",
            target_gsd_cm=4.5,
            slice_size=640,
            # The authors merge their tiles with GREEDYNMM at IOS 0.2; looser than
            # our 0.5 default, it joins pieces of one log split across a tile edge.
            merge_ios=0.2,
            class_map={"groundwood": "Fallen Tree", "uprightwood": ""},
        ),
        # Phase-2 custom model: train on customvision.ai (compact domain), export to
        # ONNX, drop model + labels into models/ — it is picked up automatically.
        ModelSource(
            key="deadtree_onnx",
            filename="dead_tree.onnx",
            label="Custom dead-tree model (Azure Custom Vision ONNX export)",
            backend="onnx",
            labels_filename="dead_tree.labels.txt",
        ),
    ]


@dataclass
class Settings:
    # --- operator attribution (NOT an account system: a name for reports /
    #     future cloud sync so a run can be traced to who produced it) ---
    operator_name: str = ""

    # --- LM Studio (Layer 2) ---
    lmstudio_url: str = "http://localhost:1234"
    lmstudio_model: str = "qwen3.5-9b"

    # --- paths (relative paths are resolved against the project root) ---
    output_dir: str = "outputs"
    models_dir: str = "models"
    source_dir: str = ""  # mission folder (SD-card dump) the console ingests
    map_tiles_dir: str = "map"  # offline map data: tiles {z}/{x}/{y}.jpg + overlays.geojson

    # Cloud Sync ability, only utilizing Azure for this project
    cloud_enabled: bool = False
    cloud_provider: str = "azure"
    azure_connection_string: str = ""  # SAS URL preferred over an account key
    azure_container: str = "wildfire-runs"
    cloud_auto_upload: bool = False
    # "results" uploads one small JSON blob per run (findings only, no imagery);
    # "full" uploads the entire run folder. Results is the default because the
    # two differ by ~40,000x on real data and imagery never needs to leave.
    cloud_upload_mode: str = "results"

    # --- report ---
    language: str = "English"
    report_max_image_pages: int = 30  # images carrying imagery in the PDF (top hazards first)
    report_detail_pages: int = 4  # of those, how many get a FULL page; the rest go in the gallery

    # --- detection / SAHI ---
    conf_threshold: float = 0.30
    slice_size: int = 1024
    # Re-encode oversized images (resolution kept, quality ladder) before
    # detection; smaller files decode faster across the batch. 0 disables.
    preprocess_max_mb: float = 2.0
    overlap_ratio: float = 0.20
    batch_size: int = 4
    perform_standard_pred: bool = True

    # --- grid density map (per-image hazard-count overlay) ---
    grid_rows: int = 6
    grid_cols: int = 8

    # --- DeepForest dead-tree detector (backend="deepforest") ---
    df_patch_size: int = 800  # DeepForest tile size for predict_tile
    df_patch_overlap: float = 0.25
    df_crown_model: str = "weecology/deepforest-tree"
    df_dead_model: str = "weecology/cropmodel-deadtrees"
    df_dead_label: str = "Dead"  # crop-model label to keep (alive/dead)
    # Resample each frame toward this ground resolution before the crown model
    # runs. DeepForest learned crowns on NEON imagery at roughly 10 cm/px; a
    # DJI L2 at 53 m gives 1.4 cm/px, and at that magnification the model calls
    # single branches and patches of dead grass "crowns" - 70 detections whose
    # median width was 0.9 m of ground. 4 cm is a measured compromise: coarse
    # enough to kill those, fine enough to keep small crowns. 0 disables.
    df_target_gsd_cm: float = 4.0

    # A floor per hazard type, applied on top of conf_threshold. Flame and smoke
    # need a much higher bar than dead trees: the fire/smoke model was trained on
    # fire photographed from the ground, and a nadir frame at 1.4 cm/px is a
    # different visual domain, so sunlit dead grass reads as flame. Measured over
    # 36 fire-free frames from real flights - at 0.30 it emitted 21 false flames
    # and 11 false smokes; at 0.60, two flames and no smoke.
    #
    # HONEST CAVEAT, do not lose it: this is tuned against false positives only.
    # There is no fire in any imagery we hold, so nobody has measured what 0.60
    # costs in RECALL. It buys a usable flame marker at an unmeasured price, and
    # the price should be measured before anyone relies on it. Lower a class to
    # 0 to fall back to conf_threshold alone.
    class_conf_thresholds: dict[str, float] = field(
        default_factory=lambda: {"Flame": 0.60, "Smoke": 0.60})

    # --- console display severity (UI-only badge; no risk field in data/PDF) ---
    # Dead trees are the primary target, so severity = avg dead trees per image.
    # Calibrated on real imagery: 67 frames of the May 2025 flight run 27..102
    # dead trees each, median 60. The old 10 / 3 graded everything High.
    # Per-frame densities for the display-only severity badge, calibrated on 49
    # real frames run through the full pipeline (2026-11-05) at about the 85th /
    # 22nd percentile of each count. The dead-tree pair was 80 / 45 until the
    # crown model started seeing frames at its training scale: that cut its count
    # from ~70 to ~20 per frame, and 80 / 45 would have graded nearly everything
    # Low. Recalibrate whenever a detector or its scale changes.
    severity_deadtrees_high: float = 39.0  # >= this per image -> High
    severity_deadtrees_medium: float = 13.0  # >= this per image -> Medium
    severity_fallen_high: float = 46.0  # fallen trees per image -> High (0 = not graded)
    severity_fallen_medium: float = 7.0  # fallen trees per image -> Medium (0 = not graded)

    # --- ONNX detector (backend="onnx", e.g. Azure Custom Vision export) ---
    onnx_input_size: int = 640  # fallback when the model input has dynamic H/W
    onnx_normalize: str = "0-255"  # "0-255" (Custom Vision) | "0-1" | "imagenet"
    onnx_channel_order: str = "RGB"  # or "BGR", per the exported model's training
    onnx_nms_iou: float = 0.45  # IoU threshold merging tile detections

    # --- models (primary dead-tree + secondary fire/smoke) ---
    model_sources: list[ModelSource] = field(default_factory=_default_model_sources)

    # paths
    def _resolve(self, p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else (PROJECT_ROOT / path)

    @property
    def output_path(self) -> Path:
        return self._resolve(self.output_dir)

    @property
    def models_path(self) -> Path:
        return self._resolve(self.models_dir)

    def model_path_for(self, filename: str) -> Path:
        return self.models_path / filename

    def ensure_dirs(self) -> None:
        self.output_path.mkdir(parents=True, exist_ok=True)
        self.models_path.mkdir(parents=True, exist_ok=True)

    def save(self, path: Optional[Path] = None) -> None:
        path = path or SETTINGS_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


def _scalar_keys() -> set[str]:
    return {f.name for f in fields(Settings) if f.name != "model_sources"}


def _modelsource_keys() -> set[str]:
    return {f.name for f in fields(ModelSource)}


def load_settings(path: Optional[Path] = None) -> Settings:
    """Load Settings from JSON, creating settings.json from the example if absent."""
    path = path or SETTINGS_PATH
    if not path.exists():
        if EXAMPLE_PATH.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(EXAMPLE_PATH, path)
        else:
            s = Settings()
            s.ensure_dirs()
            return s

    data = json.loads(path.read_text(encoding="utf-8"))

    scalar_keys = _scalar_keys()
    kwargs = {k: v for k, v in data.items() if k in scalar_keys}

    if isinstance(data.get("model_sources"), list):
        ms_keys = _modelsource_keys()
        sources = []
        for entry in data["model_sources"]:
            if isinstance(entry, dict):
                sources.append(ModelSource(**{k: v for k, v in entry.items() if k in ms_keys}))
        if sources:
            # A settings.json that lists model_sources used to REPLACE the
            # defaults outright, so a model added in a release never reached an
            # existing install. Append any default whose key the file lacks; a
            # source the operator turned off is kept, with its flag, as written.
            have = {src.key for src in sources}
            sources += [d for d in _default_model_sources() if d.key not in have]
            kwargs["model_sources"] = sources

    settings = Settings(**kwargs)
    settings.ensure_dirs()
    return settings
