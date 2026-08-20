"""EXIF GPS extraction for drone photos (JPG + TIFF), with optional GeoTIFF fallback.

Primary path: Pillow's modern API ``getexif().get_ifd(ExifTags.IFD.GPSInfo)``
(the legacy ``_getexif()`` / ``exif[0x8825]`` access was removed in Pillow 12).
Coordinates arrive as degree/minute/second rationals plus N/S/E/W refs and must
be converted to signed decimal degrees manually. Many images simply have no GPS,
so every entry point returns ``None`` rather than raising.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from PIL import ExifTags, Image

# DJI writes its XMP block near the head of the file; reading a bounded
# prefix avoids pulling a 9 MB frame into memory for one attribute.
_XMP_SCAN_BYTES = 128 * 1024


def _ratio_to_float(x) -> float:
    """Coerce a Pillow IFDRational / (num, den) tuple / number to float."""
    try:
        return float(x)
    except (TypeError, ValueError):
        try:
            num, den = x
            return float(num) / float(den) if den else 0.0
        except Exception:
            return 0.0


def _dms_to_decimal(dms, ref: Optional[str]) -> Optional[float]:
    """(deg, min, sec) rationals + N/S/E/W ref -> signed decimal degrees."""
    if dms is None:
        return None
    try:
        if isinstance(dms, (list, tuple)):
            deg = _ratio_to_float(dms[0])
            minute = _ratio_to_float(dms[1]) if len(dms) > 1 else 0.0
            second = _ratio_to_float(dms[2]) if len(dms) > 2 else 0.0
        else:  # already-decimal single rational
            deg, minute, second = _ratio_to_float(dms), 0.0, 0.0
    except (IndexError, TypeError, ValueError):
        return None
    dec = deg + minute / 60.0 + second / 3600.0
    if ref and str(ref).strip().upper() in ("S", "W"):
        dec = -dec
    return dec


def _gps_ifd_named(path: str | Path) -> Optional[dict]:
    """Return the GPS sub-IFD as a name-keyed dict, or None. Works for JPG and TIFF."""
    with Image.open(path) as img:
        exif = img.getexif()
        if not exif:
            return None
        gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo)
        if not gps_ifd:
            return None
        return {ExifTags.GPSTAGS.get(k, k): v for k, v in gps_ifd.items()}


def extract_gps(path: str | Path) -> Optional[tuple[float, float]]:
    """Return (lat, lon) in decimal degrees, or None if no usable GPS.

    Tries Pillow first, then ``exifread`` as a fallback for awkward files.
    """
    # --- Pillow ---
    try:
        gps = _gps_ifd_named(path)
    except Exception:
        gps = None
    if gps:
        lat = _dms_to_decimal(gps.get("GPSLatitude"), gps.get("GPSLatitudeRef", "N"))
        lon = _dms_to_decimal(gps.get("GPSLongitude"), gps.get("GPSLongitudeRef", "E"))
        if lat is not None and lon is not None and (lat, lon) != (0.0, 0.0):
            return (lat, lon)

    # --- exifread fallback ---
    try:
        import exifread

        with open(path, "rb") as fh:
            tags = exifread.process_file(fh, details=False)
        lat_t = tags.get("GPS GPSLatitude")
        lon_t = tags.get("GPS GPSLongitude")
        if lat_t and lon_t:
            lat_ref = str(tags.get("GPS GPSLatitudeRef", "N"))
            lon_ref = str(tags.get("GPS GPSLongitudeRef", "E"))
            lat = _dms_to_decimal([_ratio_to_float(r) for r in lat_t.values], lat_ref)
            lon = _dms_to_decimal([_ratio_to_float(r) for r in lon_t.values], lon_ref)
            if lat is not None and lon is not None:
                return (lat, lon)
    except Exception:
        pass

    return None


def extract_altitude(path: str | Path) -> Optional[float]:
    """GPS altitude in meters (negative = below sea level), or None."""
    try:
        with Image.open(path) as img:
            gps = img.getexif().get_ifd(ExifTags.IFD.GPSInfo)
    except Exception:
        return None
    if not gps:
        return None
    alt = gps.get(6)  # GPSAltitude
    if alt is None:
        return None
    alt = _ratio_to_float(alt)
    if gps.get(5) == 1:  # GPSAltitudeRef: 1 => below sea level
        alt = -alt
    return alt


def relative_altitude(path: str | Path) -> Optional[float]:
    """Height above the TAKEOFF POINT in meters, from the DJI XMP block.

    Not the same thing as `extract_altitude`, which reports GPS altitude above
    sea level - 1354 m over Canmore, where the drone was flying 53 m over the
    trees. Scale depends on height above the canopy, so only this one is usable
    for it. Non-DJI images have no such tag and return None.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(_XMP_SCAN_BYTES)
    except OSError:
        return None
    m = re.search(rb'RelativeAltitude="([+-]?[0-9.]+)"', head)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def ground_sample_distance_cm(path: str | Path) -> Optional[float]:
    """Centimeters of ground per pixel, or None when the EXIF cannot say.

    Detectors are trained at a scale, and a model shown imagery 7x finer than it
    was trained on finds the wrong things: DeepForest at NEON's ~10 cm/px looks
    for whole crowns, and on 1.4 cm/px drone frames the same shapes are single
    branches. Knowing the real GSD is what lets a detector correct for that.

        GSD = sensor_width * height / (focal_length * image_width)

    and since 35mm-equivalent focal length is DEFINED by sensor_width/focal =
    36mm/f35, this needs no per-camera sensor table:

        GSD = (36 / f35) * height / image_width

    Deliberately approximate. f35 is rounded in EXIF (24 here, giving 1.51 cm/px
    against 1.42 from the L2's true 17.3 mm sensor), and it assumes level
    ground. Both are far inside the tolerance for picking a scale factor.
    """
    height = relative_altitude(path)
    if not height or height <= 0:
        return None
    try:
        with Image.open(path) as img:
            width = img.size[0]
            f35 = img.getexif().get_ifd(ExifTags.IFD.Exif).get(41989)  # FocalLengthIn35mmFilm
    except Exception:
        return None
    if not f35 or not width:
        return None
    f35 = _ratio_to_float(f35)
    if f35 <= 0:
        return None
    return (36.0 / f35) * height / width * 100.0


def extract_timestamp(path: str | Path) -> Optional[str]:
    """Best-effort capture time: DateTimeOriginal, else GPS date stamp."""
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
            if exif_ifd:
                dto = exif_ifd.get(36867)  # DateTimeOriginal 'YYYY:MM:DD HH:MM:SS'
                if dto:
                    return str(dto)
            gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
            if gps and 29 in gps:  # GPSDateStamp
                return str(gps[29])
    except Exception:
        return None
    return None


# --------------------------------------------------------------------- DJI MRK
# DJI RTK drones write a Timestamp.MRK beside the photos: one line per exposure
#   <seq> <gps_sec> [<week>] <N>,N <E>,E <V>,V <lat>,Lat <lon>,Lon <h>,Ellh <stds> <Q>,Q
# The RTK position (cm-grade when Q=50 'fixed') beats EXIF GPS (m-grade), so we
# prefer it whenever a sibling .MRK row matches the photo's sequence number.

_MRK_CACHE: dict[tuple[str, float], dict[int, tuple[float, float, Optional[float]]]] = {}


def parse_mrk(path: str | Path) -> dict[int, tuple[float, float, Optional[float]]]:
    """Parse a DJI Timestamp.MRK -> {photo_seq: (lat, lon, ellipsoid_height)}."""
    path = Path(path)
    key = (str(path), path.stat().st_mtime)
    if key in _MRK_CACHE:
        return _MRK_CACHE[key]
    rows: dict[int, tuple[float, float, Optional[float]]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if not parts:
            continue
        try:
            seq = int(parts[0])
        except ValueError:
            continue
        lat = lon = alt = None
        for p in parts:
            try:
                if p.endswith(",Lat"):
                    lat = float(p[:-4])
                elif p.endswith(",Lon"):
                    lon = float(p[:-4])
                elif p.endswith(",Ellh"):
                    alt = float(p[:-5])
            except ValueError:
                continue
        if lat is not None and lon is not None:
            rows[seq] = (lat, lon, alt)
    _MRK_CACHE[key] = rows
    return rows


def _dji_photo_seq(name: str) -> Optional[int]:
    """Photo sequence from a DJI filename: DJI_20250502142325_0031_D.JPG -> 31."""
    m = re.search(r"_(\d{3,4})(?:_[A-Z]+)?\.[A-Za-z]+$", name)
    return int(m.group(1)) if m else None


def mrk_location(path: str | Path) -> Optional[tuple[float, float, Optional[float]]]:
    """RTK (lat, lon, alt) for an image from a sibling .MRK file, or None."""
    path = Path(path)
    seq = _dji_photo_seq(path.name)
    if seq is None:
        return None
    try:
        candidates = [p for p in path.parent.iterdir() if p.suffix.lower() == ".mrk"]
    except OSError:
        return None
    for mrk in sorted(candidates):
        try:
            row = parse_mrk(mrk).get(seq)
        except Exception:
            continue
        if row:
            return row
    return None


def extract_camera(path: str | Path) -> Optional[str]:
    """Drone/camera model from EXIF Make + Model, e.g. 'DJI FC3582'.

    This is the aircraft's camera unit, not the computer running detection —
    the UI shows it as the capture device for the flight.
    """
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            make = str(exif.get(271, "") or "").strip()   # Make
            model = str(exif.get(272, "") or "").strip()  # Model
    except Exception:
        return None
    if model.lower().startswith(make.lower()) and make:
        return model or None  # some vendors repeat the make inside the model
    combo = " ".join(p for p in (make, model) if p)
    return combo or None


def geotiff_center_lonlat(path: str | Path) -> Optional[tuple[float, float]]:
    """Center (lon, lat) in WGS84 from GeoTIFF georeferencing, or None.

    Only used when EXIF GPS is absent and ``rasterio`` is installed.
    """
    try:
        import rasterio
        from rasterio.warp import transform as warp_transform
    except Exception:
        return None
    try:
        with rasterio.open(path) as ds:
            if ds.crs is None or ds.transform is None or ds.transform.is_identity:
                return None
            cx, cy = ds.transform * (ds.width / 2.0, ds.height / 2.0)
            lon, lat = warp_transform(ds.crs, "EPSG:4326", [cx], [cy])
            return (lon[0], lat[0])
    except Exception:
        return None


def get_location(path: str | Path) -> Optional[tuple[float, float]]:
    """Unified location lookup: EXIF GPS first, then GeoTIFF (if rasterio available).

    Returns (lat, lon) in decimal degrees, or None.
    """
    coords = extract_gps(path)
    if coords is not None:
        return coords
    ll = geotiff_center_lonlat(path)
    if ll is not None:
        lon, lat = ll
        return (lat, lon)
    return None
