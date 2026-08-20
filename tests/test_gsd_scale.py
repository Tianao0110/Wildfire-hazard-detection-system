"""Ground sample distance, and resampling a frame to the scale a model expects.

A detector is only valid at the scale it learned. DeepForest learned crowns on
NEON imagery near 10 cm/px; a DJI L2 flying 53 m records 1.4 cm/px, seven times
finer, and at that magnification the shapes that match a "crown" are branches,
shrubs and patches of dead grass. Measured on one real frame: 70 detections with
a MEDIAN width of 0.9 m of ground, boxes sitting on grass and shadow.

These tests cover the arithmetic and the decision, not the model: whether a GSD
can be read at all, and whether the detector resamples, passes through, or
refuses to guess.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from src.wildfire.config import Settings
from src.wildfire.deepforest_detector import DeepForestDeadTreeDetector
from src.wildfire.gps import ground_sample_distance_cm, relative_altitude

XMP = ('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:Description '
       'drone-dji:RelativeAltitude="{alt:+.3f}"/></x:xmpmeta>')


def _frame(path, width=2000, height=1500, alt=53.139, f35=24):
    """A JPEG carrying the two tags a GSD needs, like a DJI frame does."""
    im = Image.fromarray(np.zeros((height, width, 3), np.uint8))
    kwargs = {}
    if f35 is not None:
        exif = im.getexif()
        exif.get_ifd(0x8769)[41989] = f35  # FocalLengthIn35mmFilm
        kwargs["exif"] = exif
    if alt is not None:
        kwargs["xmp"] = XMP.format(alt=alt).encode()
    im.save(path, **kwargs)
    return path


# ------------------------------------------------------------------ the maths


def test_gsd_matches_the_hand_calculation(tmp_path):
    """GSD = (36 / f35) * height / width. 24 mm at 53.139 m over 2000 px."""
    p = _frame(tmp_path / "a.jpg")
    assert ground_sample_distance_cm(p) == pytest.approx(
        (36 / 24) * 53.139 / 2000 * 100, rel=1e-6)


def test_gsd_tracks_altitude_and_lens(tmp_path):
    low = ground_sample_distance_cm(_frame(tmp_path / "low.jpg", alt=25))
    high = ground_sample_distance_cm(_frame(tmp_path / "high.jpg", alt=100))
    assert high > low  # higher flight -> more ground per pixel

    wide = ground_sample_distance_cm(_frame(tmp_path / "wide.jpg", f35=16))
    tele = ground_sample_distance_cm(_frame(tmp_path / "tele.jpg", f35=48))
    assert wide > tele  # longer lens -> less ground per pixel


def test_gsd_is_none_when_the_exif_cannot_say(tmp_path):
    """A guessed scale is worse than the one the operator actually flew."""
    assert ground_sample_distance_cm(_frame(tmp_path / "noalt.jpg", alt=None)) is None
    assert ground_sample_distance_cm(_frame(tmp_path / "nolens.jpg", f35=None)) is None
    assert ground_sample_distance_cm(tmp_path / "missing.jpg") is None
    assert relative_altitude(_frame(tmp_path / "b.jpg", alt=None)) is None


def test_relative_altitude_is_not_gps_altitude(tmp_path):
    """1354 m above sea level is the Canmore valley floor; 53 m is the flight."""
    assert relative_altitude(_frame(tmp_path / "c.jpg", alt=53.139)) == 53.139


# ------------------------------------------------------- the resample decision


def _detector(target_cm):
    """The detector's scaling behaviour without loading 5.6 s of model."""
    d = object.__new__(DeepForestDeadTreeDetector)
    d.settings = Settings(df_target_gsd_cm=target_cm, model_sources=[])
    d._log = None
    return d


def test_a_fine_frame_is_resampled_and_the_factor_returns_boxes_to_native(tmp_path):
    p = _frame(tmp_path / "fine.jpg", width=4000, height=3000, alt=53.139)
    gsd = ground_sample_distance_cm(p)
    assert gsd == pytest.approx(1.99, abs=0.01)  # finer than the 4 cm target

    with _detector(4.0)._at_training_scale(str(p)) as (src, scale):
        assert src != str(p), "a frame this fine must not go in at native scale"
        with Image.open(src) as small:
            assert small.size[0] < 4000
            assert scale == pytest.approx(4000 / small.size[0])
            # resampled to roughly the target, give or take integer rounding
            assert gsd * scale == pytest.approx(4.0, rel=0.02)


def test_a_frame_already_at_the_target_is_left_alone(tmp_path):
    p = _frame(tmp_path / "coarse.jpg", width=200, height=150)
    assert ground_sample_distance_cm(p) > 30  # far coarser than any target
    with _detector(4.0)._at_training_scale(str(p)) as (src, scale):
        assert (src, scale) == (str(p), 1.0), "resampling UP invents detail"


def test_no_gsd_means_no_resampling(tmp_path):
    p = _frame(tmp_path / "plain.jpg", alt=None)
    with _detector(4.0)._at_training_scale(str(p)) as (src, scale):
        assert (src, scale) == (str(p), 1.0)


def test_the_feature_can_be_switched_off(tmp_path):
    p = _frame(tmp_path / "fine.jpg", width=4000, height=3000)
    with _detector(0)._at_training_scale(str(p)) as (src, scale):
        assert (src, scale) == (str(p), 1.0)


def test_an_unreadable_frame_falls_back_instead_of_raising(tmp_path):
    """Detection must survive a frame Pillow cannot resample."""
    p = tmp_path / "broken.jpg"
    p.write_bytes(b"not a jpeg at all, but it does mention "
                  b'RelativeAltitude="+53.139" somewhere')
    d = _detector(4.0)
    said = []
    d._log = said.append
    with d._at_training_scale(str(p)) as (src, scale):
        assert (src, scale) == (str(p), 1.0)
