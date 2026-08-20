"""DeepForest dead-tree detector (primary), implementing the common detector interface.

DeepForest is a torchvision/RetinaNet tree-crown detector. We pair its pretrained
crown model (``weecology/deepforest-tree``) with the alive/dead CropModel
(``weecology/cropmodel-deadtrees``): detect all crowns, classify each alive/dead,
keep only the dead ones, and emit them as ``display="Dead Tree"`` detections.

DeepForest does its OWN tiling (predict_tile), so it does not use SAHI. It runs
alongside the SAHI+YOLO fire/smoke detector; the pipeline merges the outputs.

Two DeepForest 2.1.0 quirks are handled here:
  - CropModel.load_model() (PyTorchModelHubMixin) does not rebuild the ResNet on
    newer huggingface_hub; we repair it by calling create_model() and loading the
    HF safetensors weights into crop.model.
  - predict_tile's Lightning rich progress bar crashes on a non-UTF-8 (e.g. GBK)
    Windows console; we disable it via create_trainer(enable_progress_bar=False).
"""

from __future__ import annotations

import inspect
import os
from typing import Callable, Optional

from .config import Settings
from .device import pick_device
from .types import Detection

ProgressFn = Optional[Callable[[str], None]]


def deepforest_available() -> bool:
    """True if the deepforest package can be imported."""
    try:
        import deepforest  # noqa: F401

        return True
    except Exception:
        return False


def _configure_quiet() -> None:
    """Reduce Lightning/rasterio noise and enable Tensor-Core matmul on the GPU."""
    import logging
    import warnings

    try:
        import torch

        torch.set_float32_matmul_precision("high")  # Tensor Cores on any modern NVIDIA GPU
    except Exception:
        pass
    for name in ("pytorch_lightning", "lightning.pytorch", "lightning"):
        logging.getLogger(name).setLevel(logging.ERROR)
    warnings.filterwarnings("ignore", category=UserWarning, module="pytorch_lightning")
    warnings.filterwarnings("ignore", module="rasterio")


def _repair_crop_model(crop, repo_id: str) -> None:
    """Rebuild crop.model and load real weights (DeepForest 2.1.0 + new hub bug).

    After CropModel.load_model, crop.model can be a metadata dict instead of the
    ResNet. Rebuild the architecture and load the HF safetensors weights.
    """
    import torch

    if isinstance(getattr(crop, "model", None), torch.nn.Module):
        return  # already fine on this version
    from huggingface_hub import hf_hub_download
    from safetensors.torch import load_file

    num_classes = int(getattr(crop, "num_classes", 2))
    crop.create_model(num_classes=num_classes)
    sd = load_file(hf_hub_download(repo_id, "model.safetensors"))
    model_sd = {k[len("model."):]: v for k, v in sd.items() if k.startswith("model.")}
    crop.model.load_state_dict(model_sd, strict=False)


class DeepForestDeadTreeDetector:
    """Crown detection (DeepForest) + alive/dead classification -> dead-tree boxes."""

    name = "deepforest-deadtree"

    def __init__(self, settings: Settings, device: str | None = None, log: ProgressFn = None):
        self.settings = settings
        self.device = device or pick_device()
        self._log = log
        self._model = None
        self._crop = None
        self._load()

    def _say(self, msg: str) -> None:
        if callable(self._log):
            self._log(msg)

    # ------------------------------------------------------------------ loading
    def _load(self) -> None:
        _configure_quiet()
        from deepforest import main as df_main

        s = self.settings
        self._say(f"[deadtree] loading DeepForest crown model ({s.df_crown_model})...")
        model = df_main.deepforest()
        try:
            model.load_model(s.df_crown_model)
        except Exception:
            if hasattr(model, "use_release"):
                model.use_release()
            else:
                raise
        # Disable the Lightning rich progress bar (crashes on non-UTF-8 consoles)
        # and the logger tip. predict_tile reuses this trainer.
        try:
            model.create_trainer(enable_progress_bar=False, logger=False)
        except Exception:
            pass
        try:
            model.to("cuda" if self.device.startswith("cuda") else self.device)
        except Exception:
            pass
        self._model = model

        # Alive/dead CropModel, with the load repair.
        self._say(f"[deadtree] loading alive/dead classifier ({s.df_dead_model})...")
        try:
            from deepforest.model import CropModel

            crop = CropModel.load_model(s.df_dead_model)
            _repair_crop_model(crop, s.df_dead_model)
            crop.eval()
            self._crop = crop
        except Exception as e:
            self._crop = None
            self._say(
                f"[deadtree] alive/dead classifier failed to load ({e}); "
                "dead-tree detection disabled for this run."
            )

    # ------------------------------------------------------------------ predict
    def _predict_tile(self, image_path: str, crop_model=None):
        """Tiled crown detection. `crop_model` opts into DeepForest classifying
        every crown it finds - the slow path, kept only as a fallback."""
        m = self._model
        s = self.settings
        kwargs = dict(patch_size=s.df_patch_size, patch_overlap=s.df_patch_overlap)
        if crop_model is not None:
            kwargs["crop_model"] = crop_model
        sig = set(inspect.signature(m.predict_tile).parameters)
        if "path" in sig:
            return m.predict_tile(path=image_path, **kwargs)
        if "raster_path" in sig:
            return m.predict_tile(raster_path=image_path, **kwargs)
        return m.predict_tile(image_path, **kwargs)

    def _classify(self, crowns, image_path: str):
        """Alive/dead classify a crown frame; None if this DeepForest cannot.

        `predict_tile(crop_model=...)` classifies EVERY crown it found, and the
        caller then discards everything below conf_threshold. Measured on a
        5280x3956 DJI L2 frame: 1,406 crowns found, 270 (19%) above the
        threshold - so about 17 of the 44 seconds went into classifying trees
        the run would never report. The classifier runs per crown, so dropping
        one first cannot change the verdict on any other: filtering first is the
        same answer for 39% less work (43.6 s -> 26.8 s, 70 detections either
        way).

        This calls the private helper that `predict_tile` itself calls. If a
        future DeepForest moves it, say so and return None - the caller falls
        back to the slow path, because a slow run beats no dead-tree detection.
        """
        try:
            from deepforest import predict as df_predict

            wrapper = getattr(df_predict, "_crop_models_wrapper_", None)
            if wrapper is None:
                self._say("[deadtree] this deepforest has no _crop_models_wrapper_; "
                          "classifying every crown instead.")
                return None
            frame = crowns.copy()
            frame.root_dir = os.path.dirname(str(image_path))
            return wrapper(self._crop, self._model.trainer, frame)
        except Exception as e:
            self._say(f"[deadtree] two-stage classify failed ({type(e).__name__}: {e}); "
                      "classifying every crown instead.")
            return None

    @staticmethod
    def _bbox(row):
        if all(c in row and row[c] is not None for c in ("xmin", "ymin", "xmax", "ymax")):
            return (float(row["xmin"]), float(row["ymin"]), float(row["xmax"]), float(row["ymax"]))
        geom = row.get("geometry") if hasattr(row, "get") else None
        if geom is not None and hasattr(geom, "bounds"):
            minx, miny, maxx, maxy = geom.bounds
            return (float(minx), float(miny), float(maxx), float(maxy))
        return None

    def predict(self, image) -> list[Detection]:
        # Without a working alive/dead classifier we cannot identify DEAD trees, so
        # emit nothing rather than flagging every crown (avoids false positives).
        if self._model is None or self._crop is None:
            return []

        s = self.settings

        # Two calls on purpose: find the crowns, drop the ones this run would
        # discard anyway, and only then pay the alive/dead classifier. See
        # _classify for the measurement. The fallback re-runs the tiling, which
        # is slower than never having split - acceptable for a path that only
        # fires if DeepForest moves the helper, and it announces itself.
        crowns = self._predict_tile(str(image))
        df = None
        if crowns is not None and len(crowns) and "score" in crowns.columns:
            keep = crowns[crowns["score"] >= s.conf_threshold]
            if len(keep) == 0:
                return []  # nothing clears the threshold; nothing to classify
            df = self._classify(keep, str(image))
        if df is None:
            df = self._predict_tile(str(image), crop_model=self._crop)
        if df is None or len(df) == 0 or "cropmodel_label" not in df.columns:
            return []

        dead_label = s.df_dead_label.lower()
        detections: list[Detection] = []
        for _, row in df.iterrows():
            crown_score = float(row.get("score", 1.0) or 1.0)
            if crown_score < s.conf_threshold:
                continue
            if str(row["cropmodel_label"]).lower() != dead_label:
                continue  # keep only crowns classified as dead
            box = self._bbox(row)
            if box is None:
                continue
            score = float(row.get("cropmodel_score", crown_score) or crown_score)
            detections.append(
                Detection(cls_name="dead_tree", display="Dead Tree", score=score, xyxy=box)
            )
        return detections
