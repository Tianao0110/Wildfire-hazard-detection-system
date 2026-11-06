"""PDF field report builder (Layer 2) using ReportLab platypus.

Layout (landscape Letter):
  - Cover page: title, flight/batch info, totals, detection breakdown.
  - Per-image pages: original + annotated (crown highlight) + grid map side-by-side,
    plus a GPS/metadata + per-type detection-count table.
  - Summary page: batch statistics table + AI analysis/recommendations (Qwen via
    LM Studio; a graceful fallback note is used if the LLM text is unavailable).

No risk classification (per spec) - detections are counted and flagged, not scored.
Images are downscaled to a display copy before embedding so the PDF stays small.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional


def timestamped_report_path(out_dir: str | Path) -> Path:
    """Unique report filename inside a run folder (regenerating never overwrites)."""
    base = Path(out_dir) / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    path, n = base, 2
    while path.exists():  # two generations within the same second
        path = base.with_name(f"{base.stem}_{n}.pdf")
        n += 1
    return path


def latest_report(out_dir: str | Path) -> Optional[Path]:
    """Newest report PDF in a run folder (report_*.pdf, or the legacy report.pdf)."""
    candidates = sorted(Path(out_dir).glob("report*.pdf"), key=lambda p: p.stat().st_mtime)
    return candidates[-1] if candidates else None

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as canvas_mod
from reportlab.platypus import (
    Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from .types import BatchResult, ImageResult

PAGE = landscape(letter)  # (792, 612) pt
_styles = getSampleStyleSheet()
_H1 = _styles["Title"]
_H2 = _styles["Heading2"]
_BODY = _styles["BodyText"]
_SMALL = ParagraphStyle("small", parent=_styles["Normal"], fontSize=8, textColor=colors.grey)

HEADER = colors.HexColor("#2c3e50")
ACCENT = colors.HexColor("#c0392b")

# Branding read from the branding/ folder (same as the console) so the report
# header/title carry the deployment's logo + name. Set by build_report().
_BRAND: dict = {"name": "Wildfire Hazardous Tree Mapping", "logo": None}


def _load_branding(branding_dir) -> dict:
    """Read branding/brand.json + auto-detect logo.* for the PDF header."""
    import json
    from pathlib import Path as _P

    brand = {"name": "Wildfire Hazardous Tree Mapping", "logo": None}
    if not branding_dir:
        return brand
    bdir = _P(branding_dir)
    bj = bdir / "brand.json"
    if bj.exists():
        try:
            cfg = json.loads(bj.read_text(encoding="utf-8"))
            brand["name"] = cfg.get("app_name") or brand["name"]
        except Exception:
            pass
    for ext in ("png", "jpg", "jpeg", "svg", "webp"):
        cand = bdir / f"logo.{ext}"
        if cand.exists() and ext != "svg":  # reportlab can't embed SVG directly
            brand["logo"] = str(cand)
            break
    return brand


class _NumberedCanvas(canvas_mod.Canvas):
    """Adds 'Page X of Y' and a footer banner (two-pass over saved pages)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._saved = []

    def showPage(self):
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            self._footer(total)
            super().showPage()
        super().save()

    def _footer(self, total):
        w, _ = PAGE
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.grey)
        # the deployment's name, like the banner and the cover - it was a fixed
        # string, so a rebranded report still signed every page with the old one
        self.drawString(0.6 * inch, 0.4 * inch,
                        _pdf_safe(_BRAND.get("name") or "Wildfire Hazardous Tree Mapping"))
        self.drawRightString(w - 0.6 * inch, 0.4 * inch, f"Page {self._pageNumber} of {total}")


def _banner(canvas, doc):
    w, h = PAGE
    canvas.saveState()
    canvas.setFillColor(HEADER)
    canvas.rect(0, h - 0.45 * inch, w, 0.45 * inch, fill=1, stroke=0)
    x = 0.6 * inch
    logo = _BRAND.get("logo")
    if logo:
        try:
            ir = ImageReader(logo)
            iw, ih = ir.getSize()
            lh = 0.3 * inch  # fit logo height into the banner
            lw = lh * iw / float(ih)
            canvas.drawImage(ir, x, h - 0.38 * inch, width=lw, height=lh,
                             mask="auto", preserveAspectRatio=True)
            x += lw + 8
        except Exception:
            pass
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawString(x, h - 0.30 * inch, _pdf_safe(_BRAND.get("name", "")).upper())
    canvas.restoreState()


def _display_copy(src: Optional[str], assets: Path, max_px: int = 1200) -> Optional[str]:
    """Downscale an image to a display-size JPG in `assets`; return its path or None."""
    if not src:
        return None
    p = Path(src)
    if not p.exists():
        return None
    try:
        import hashlib

        from PIL import Image as PILImage

        assets.mkdir(parents=True, exist_ok=True)
        # Cache key includes the FULL path: annotated/x_confirmed.jpg and
        # gridmaps/x_confirmed.jpg share a stem and used to overwrite each
        # other here, putting the grid map in the "Detections" column.
        tag = hashlib.sha1(str(p.resolve()).encode("utf-8")).hexdigest()[:10]
        out = assets / f"disp_{tag}_{p.stem}.jpg"
        with PILImage.open(p) as im:
            im = im.convert("RGB")
            im.thumbnail((max_px, max_px))
            im.save(out, "JPEG", quality=80)
        return str(out)
    except Exception:
        return str(p)


def _fit_image(path: Optional[str], max_w: float, max_h: float):
    """An Image flowable scaled to fit (max_w, max_h) keeping aspect ratio, or a dash."""
    if not path or not Path(path).exists():
        return Paragraph("-", _SMALL)
    iw, ih = ImageReader(path).getSize()
    ratio = ih / float(iw)
    w = max_w
    h = w * ratio
    if h > max_h:
        h = max_h
        w = h / ratio
    return Image(path, width=w, height=h)


# LLM output loves typographic punctuation; the built-in PDF fonts are
# Latin-1/WinAnsi only, so anything outside renders as a black box.
_PDF_CHAR_MAP = str.maketrans({
    "—": "-", "–": "-", "‑": "-", "−": "-",  # dashes/minus
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "…": "...", "•": "-", " ": " ", "→": "->",
    "≤": "<=", "≥": ">=", "×": "x", "≈": "~",
})


def _pdf_safe(text: str) -> str:
    """Map typographic chars to ASCII, then drop anything Latin-1 can't hold."""
    return (text.translate(_PDF_CHAR_MAP)
            .encode("latin-1", "replace").decode("latin-1"))


def _counts_by_type(im: ImageResult) -> dict:
    return dict(Counter(d.display for d in im.detections))


def _fmt_counts(counts: dict) -> str:
    """'{"Dead Tree": 68}' -> 'Dead Tree: 68'."""
    if not counts:
        return "-"
    return ", ".join(f"{k}: {v}" for k, v in counts.items())


def build_summary_text(batch: BatchResult) -> str:
    """Structured survey facts fed to the LLM.

    The analysis can only be as professional as the data it is grounded in, so
    this includes flight metadata, spatial extent, density and confidence
    statistics, review status, and a ranked hotspot list - not just totals.
    """
    s = batch.stats
    bi = batch.batch_info
    reviewed = bool(bi.get("review"))

    camera = next((im.camera for im in batch.images if im.camera), None)
    capture = next((im.timestamp for im in batch.images if im.timestamp), None)
    gps_pts = [im.gps for im in batch.images if im.gps]
    alts = [im.altitude for im in batch.images if im.altitude is not None]

    lines = [
        "=== FLIGHT ===",
        f"Batch: {bi.get('batch_label', 'n/a')}; processed {bi.get('generated_at', '')}.",
        f"Aircraft/camera: {camera or 'unknown'}; first capture time: {capture or 'unknown'}.",
        f"Images: {s.get('images_processed', 0)} ({s.get('images_with_gps', 0)} GPS-tagged); "
        f"altitude range: {f'{min(alts):.0f}-{max(alts):.0f} m' if alts else 'unknown'}.",
    ]
    if gps_pts:
        lat0, lat1 = min(p[0] for p in gps_pts), max(p[0] for p in gps_pts)
        lon0, lon1 = min(p[1] for p in gps_pts), max(p[1] for p in gps_pts)
        ext_ns = (lat1 - lat0) * 111_320
        ext_ew = (lon1 - lon0) * 111_320 * 0.63  # cos(51°) - good enough for Alberta
        lines.append(f"Surveyed extent: ~{ext_ns:.0f} m N-S x {ext_ew:.0f} m E-W "
                     f"(bbox {lat0:.5f},{lon0:.5f} to {lat1:.5f},{lon1:.5f}).")

    n_img = max(1, s.get("images_processed", 0))
    counts = s.get("detections_by_type", {})
    lines += [
        "",
        "=== DETECTIONS ===",
        f"Status: {'REVIEWER-CONFIRMED boxes (post-review)' if reviewed else 'UNREVIEWED AI proposals'}.",
        f"Totals: {s.get('total_detections', 0)} across {s.get('flagged_images', 0)} flagged images: "
        + (", ".join(f"{v} {k}" for k, v in counts.items()) or "none") + ".",
        f"Dead-tree density: mean {counts.get('Dead Tree', 0) / n_img:.1f} per image.",
        "NOTE: consecutive drone photos overlap, so the same tree can be counted in several images "
        "- treat totals as indicative, not absolute stem counts.",
    ]
    if not reviewed:
        scores = [d.score for im in batch.images for d in im.detections]
        if scores:
            lines.append(f"Model confidence: mean {sum(scores) / len(scores):.2f}, "
                         f"max {max(scores):.2f} (n={len(scores)}).")

    hotspots = sorted((im for im in batch.images if im.detections),
                      key=lambda im: len(im.detections), reverse=True)
    lines += ["", "=== HOTSPOTS (ranked by detections per image) ==="]
    for im in hotspots[:15]:
        gps = f"{im.gps[0]:.5f}, {im.gps[1]:.5f}" if im.gps else "no GPS"
        lines.append(f"- {im.name}: {_counts_by_type(im)} @ {gps}")
    if len(hotspots) > 15:
        lines.append(f"... and {len(hotspots) - 15} more flagged images.")
    return "\n".join(lines)


def _review_badge(reviewed: bool):
    """A colored cover banner stating whether a human confirmed the detections."""
    if reviewed:
        text = "REVIEWED - every detection in this batch has been verified"
        fg, bg, line = "#2D5A2D", "#e3efe3", "#3A9A3A"
    else:
        text = "UNREVIEWED - AI proposals, not yet confirmed by a reviewer"
        fg, bg, line = "#8a6d1a", "#f7efd6", "#d9b64a"
    badge = Table([[text]], colWidths=[8.2 * inch])
    badge.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(bg)),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor(fg)),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor(line)),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
    ]))
    return badge


def _cover(batch: BatchResult) -> list:
    s = batch.stats
    bi = batch.batch_info
    reviewed = bool(bi.get("review"))
    story = [Spacer(1, 0.5 * inch)]
    if _BRAND.get("logo"):
        try:
            story.append(_fit_image(_BRAND["logo"], 3.0 * inch, 0.9 * inch))
            story.append(Spacer(1, 0.15 * inch))
        except Exception:
            pass
    story += [
        Paragraph(_pdf_safe(_BRAND.get("name", "Wildfire Hazardous Tree Mapping")), _H1),
        Paragraph("Drone Forest Hazard Detection - Field Report", _H2),
        Spacer(1, 0.25 * inch),
        _review_badge(reviewed),
        Spacer(1, 0.3 * inch),
    ]
    info = [
        ["Batch", str(bi.get("batch_label", "n/a"))],
    ]
    if bi.get("operator"):
        info.append(["Operator", _pdf_safe(str(bi["operator"]))])
    # The computer, when we know it. The GPU model that used to sit here is an
    # internal detail and does not belong in a report handed to a client.
    if bi.get("machine"):
        info.append(["Processed on", _pdf_safe(str(bi["machine"]))])
    info += [
        ["Generated", str(bi.get("generated_at", ""))],
        ["Images processed", str(s.get("images_processed", 0))],
        ["Flagged with hazards", str(s.get("flagged_images", 0))],
        ["Total detections", f"{s.get('total_detections', 0)}  ({_fmt_counts(s.get('detections_by_type', {}))})"],
        ["Images with GPS", str(s.get("images_with_gps", 0))],
    ]
    t = Table(info, colWidths=[2.2 * inch, 6.0 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f3f5")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d5dbdf")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story += [t, Spacer(1, 0.3 * inch),
              Paragraph("Legend: flame = red, smoke = dark slate, dead tree = bone white, "
                        "fallen tree = sky blue.",
                        _SMALL),
              PageBreak()]
    return story


def _image_page(im: ImageResult, assets: Path) -> list:
    w, h = PAGE
    usable_w = w - 1.2 * inch
    col = (usable_w - 0.3 * inch) / 3.0
    img_h = 3.0 * inch

    o = _fit_image(_display_copy(im.orig_display_path, assets), col, img_h)
    a = _fit_image(_display_copy(im.annotated_path, assets), col, img_h)
    g = _fit_image(_display_copy(im.density_path, assets), col, img_h)
    row = Table([[o, a, g], ["Original", "Detections (highlighted)", "Hazard density grid"]],
                colWidths=[col] * 3)
    row.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, 0), "TOP"),
        ("FONTSIZE", (0, 1), (-1, 1), 8), ("TEXTCOLOR", (0, 1), (-1, 1), colors.grey),
        ("TOPPADDING", (0, 1), (-1, 1), 2),
    ]))

    counts = _counts_by_type(im)
    gps = f"{im.gps[0]:.6f}, {im.gps[1]:.6f}" if im.gps else "-"
    meta = [
        ["Image", im.name, "Detections", str(len(im.detections))],
        ["GPS (lat, lon)", gps, "By type", _fmt_counts(counts)],
        ["Altitude (m)", str(im.altitude if im.altitude is not None else "-"),
         "Timestamp", str(im.timestamp or "-")],
    ]
    mt = Table(meta, colWidths=[1.6 * inch, 3.4 * inch, 1.4 * inch, 2.4 * inch])
    mt.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#dddddd")),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f4f6f7")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#f4f6f7")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))

    story = [Paragraph(f"Image: {im.name}", _H2), Spacer(1, 0.08 * inch), row,
             Spacer(1, 0.12 * inch), mt]
    if im.error:
        story.append(Paragraph(f"Error: {im.error}", _SMALL))
    story.append(PageBreak())
    return story


GALLERY_COLS, GALLERY_ROWS = 4, 2   # 8 images per contact-sheet page


def _gallery_pages(images: list[ImageResult], assets: Path) -> list:
    """Contact sheet: the annotated rendition of many images per page.

    A full `_image_page` per image is the right treatment for the few worst
    hazards and pure bulk for the rest -- 40 images meant 40 pages nobody
    scrolls through. Here each image is one annotated thumbnail plus a caption
    line (name, counts, GPS), 8 to a page, which is enough to recognise a site
    and find it in the console.
    """
    if not images:
        return []
    w, h = PAGE
    usable_w = w - 1.2 * inch
    gap = 0.12 * inch
    col = (usable_w - gap * (GALLERY_COLS - 1)) / GALLERY_COLS
    thumb_h = 1.55 * inch

    story = [Paragraph("Hazard Image Gallery", _H1),
             Paragraph("Annotated detections, highest hazard count first. Full-size "
                       "imagery and every detection stay in the review console.", _SMALL),
             Spacer(1, 0.12 * inch)]
    per_page = GALLERY_COLS * GALLERY_ROWS
    for start in range(0, len(images), per_page):
        chunk = images[start:start + per_page]
        cells, caps = [], []
        for im in chunk:
            cells.append(_fit_image(_display_copy(im.annotated_path, assets, max_px=700),
                                    col, thumb_h))
            counts = _fmt_counts(_counts_by_type(im)) or "no detections"
            gps = f"{im.gps[0]:.5f}, {im.gps[1]:.5f}" if im.gps else "no GPS"
            caps.append(Paragraph(
                f"<b>{_pdf_safe(im.name)}</b><br/>{_pdf_safe(counts)}<br/>{gps}", _SMALL))
        # Pad the last row so the table stays rectangular.
        while len(cells) % GALLERY_COLS:
            cells.append("")
            caps.append("")

        for r in range(0, len(cells), GALLERY_COLS):
            t = Table([cells[r:r + GALLERY_COLS], caps[r:r + GALLERY_COLS]],
                      colWidths=[col] * GALLERY_COLS)
            t.setStyle(TableStyle([
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
                ("VALIGN", (0, 1), (-1, 1), "TOP"),
                ("TOPPADDING", (0, 1), (-1, 1), 2),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
            ]))
            story.append(t)
        if start + per_page < len(images):
            story.append(PageBreak())
    story.append(PageBreak())
    return story


def _image_index(batch: BatchResult) -> list:
    """One compact row per image, covering EVERY image in the run.

    The gallery and detail pages are both ranked and capped, so without this
    table a reader cannot tell whether an image was clean or simply cut. ~28
    rows a page keeps a 250-image run to about nine pages.
    """
    rows = [["Image", "Captured", "GPS (lat, lon)", "Alt (m)", "Detections", "By type"]]
    for im in batch.images:
        gps = f"{im.gps[0]:.5f}, {im.gps[1]:.5f}" if im.gps else "-"
        rows.append([
            _pdf_safe(im.name), _pdf_safe(str(im.timestamp or "-")), gps,
            str(im.altitude if im.altitude is not None else "-"),
            str(len(im.detections)),
            _pdf_safe(_fmt_counts(_counts_by_type(im)) or "-"),
        ])
    t = Table(rows, repeatRows=1,
              colWidths=[2.5 * inch, 1.5 * inch, 1.8 * inch, 0.7 * inch, 0.9 * inch, 1.8 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HEADER), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d5dbdf")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f9fa")]),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return [Paragraph("Image Index", _H1),
            Paragraph(f"All {len(batch.images)} images in this run.", _SMALL),
            Spacer(1, 0.12 * inch), t]


MAP_W, MAP_H = 900, 540

# Hazard-TYPE pin colors (RGB). Keep in sync with the BGR constants in
# annotate.py and KIND in console/static/console.js. Smoke and dead tree were
# orange and yellow, only 1.48:1 apart -- unreadable next to each other and
# worse in greyscale, which is how a field report usually gets printed.
FLAME_RGB = (224, 85, 85)       # #E05555
SMOKE_RGB = (84, 110, 122)      # #546E7A
DEADTREE_RGB = (217, 205, 176)  # #D9CDB0
# Fallen tree is separated from the three above by hue, not luminance - no
# colour reaches 3:1 against all of them (see annotate.py) - plus its own icon.
FALLEN_RGB = (41, 182, 246)     # #29B6F6
CLEAR_RGB = (58, 154, 58)       # no detections


def _merc_px(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    """WGS84 -> global web-mercator pixel coordinates at `zoom` (256px tiles)."""
    import math

    scale = 256 * (2 ** zoom)
    x = (lon + 180.0) / 360.0 * scale
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * scale
    return x, y


def _satellite_backdrop(pts: list, map_dir: Optional[Path]):
    """Stitch cached offline tiles into a real satellite backdrop for the pin
    bbox. Returns (PIL image WxH, latlon->canvas transform) or None when the
    area isn't cached — the plain light canvas is the fallback, not the goal.
    """
    if not map_dir or not Path(map_dir).is_dir():
        return None
    import math

    from PIL import Image as PILImage

    map_dir = Path(map_dir)
    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    for zoom in range(17, 5, -1):  # highest cached detail wins
        xs, ys = zip(*(_merc_px(lat, lon, zoom) for lat, lon in zip(lats, lons)))
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        # viewport in mercator px: pins + 35% margin, at least 5 tiles wide,
        # locked to the canvas aspect ratio
        half_w = max((max(xs) - min(xs)) * 0.675, 640.0)
        half_h = max((max(ys) - min(ys)) * 0.675, half_w * MAP_H / MAP_W)
        half_w = max(half_w, half_h * MAP_W / MAP_H)
        half_h = half_w * MAP_H / MAP_W
        x0, x1 = cx - half_w, cx + half_w
        y0, y1 = cy - half_h, cy + half_h
        tx0, tx1 = int(x0 // 256), int(x1 // 256)
        ty0, ty1 = int(y0 // 256), int(y1 // 256)
        n_tiles = (tx1 - tx0 + 1) * (ty1 - ty0 + 1)
        if n_tiles > 48:
            continue
        needed = [(tx, ty) for tx in range(tx0, tx1 + 1) for ty in range(ty0, ty1 + 1)]
        if not all((map_dir / str(zoom) / str(tx) / f"{ty}.jpg").exists() for tx, ty in needed):
            continue

        mosaic = PILImage.new("RGB", ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256), (24, 26, 24))
        for tx, ty in needed:
            with PILImage.open(map_dir / str(zoom) / str(tx) / f"{ty}.jpg") as tile:
                mosaic.paste(tile.convert("RGB"), ((tx - tx0) * 256, (ty - ty0) * 256))
        crop = mosaic.crop((int(x0 - tx0 * 256), int(y0 - ty0 * 256),
                            int(x1 - tx0 * 256), int(y1 - ty0 * 256)))
        img = crop.resize((MAP_W, MAP_H), PILImage.LANCZOS)

        def to_canvas(lat: float, lon: float, _z=zoom, _x0=x0, _y0=y0,
                      _sx=MAP_W / (x1 - x0), _sy=MAP_H / (y1 - y0)):
            gx, gy = _merc_px(lat, lon, _z)
            return (gx - _x0) * _sx, (gy - _y0) * _sy

        return img, to_canvas, zoom
    return None


def _pin_map_png(batch: BatchResult, assets: Path,
                 map_dir: Optional[Path] = None) -> Optional[Path]:
    """Hazard map for the summary page: REAL offline satellite tiles when the
    area is cached (same imagery as the console map), else a light schematic.
    One pin per GPS-tagged image: flame red, smoke slate, dead tree bone,
    fallen tree sky blue - the most critical type in the frame.
    """
    from PIL import Image as PILImage
    from PIL import ImageDraw

    pts = []
    for im in batch.images:
        if not im.gps:
            continue
        names = {d.display for d in im.detections}
        color = (FLAME_RGB if "Flame" in names else
                 SMOKE_RGB if "Smoke" in names else
                 DEADTREE_RGB if "Dead Tree" in names else
                 FALLEN_RGB if "Fallen Tree" in names else
                 DEADTREE_RGB if im.detections else CLEAR_RGB)
        pts.append((float(im.gps[0]), float(im.gps[1]), color))
    if not pts:
        return None

    W, H = MAP_W, MAP_H
    backdrop = _satellite_backdrop([(lat, lon) for lat, lon, _ in pts], map_dir)
    if backdrop:
        img, to_canvas, zoom = backdrop
        d = ImageDraw.Draw(img)
        title = f"Offline satellite imagery (zoom {zoom}) - north up"
        attribution = "Imagery (c) Esri - Maxar, Earthstar Geographics"
    else:
        img = PILImage.new("RGB", (W, H), (255, 255, 255))
        d = ImageDraw.Draw(img)
        for x in range(0, W, 64):
            d.line([(x, 0), (x, H)], fill=(228, 228, 220))
        for y in range(0, H, 64):
            d.line([(0, y), (W, y)], fill=(228, 228, 220))
        lats = [p[0] for p in pts]
        lons = [p[1] for p in pts]
        lat_span = max(max(lats) - min(lats), 1e-4)
        lon_span = max(max(lons) - min(lons), 1e-4)

        def to_canvas(lat, lon):
            return ((0.12 + (lon - min(lons)) / lon_span * 0.76) * W,
                    (0.12 + (max(lats) - lat) / lat_span * 0.76) * H)

        title = "Relative GPS positions - north up - not to scale (no offline tiles cached)"
        attribution = ""

    for lat, lon, color in pts:
        px, py = to_canvas(lat, lon)
        # Dark fills get a white ring so they read against satellite imagery;
        # the bone dead-tree fill is too light for that and needs a dark ring
        # instead, on imagery and on white paper alike.
        light = sum(color) / 3 > 170
        d.ellipse([px - 7, py - 7, px + 7, py + 7], fill=color,
                  outline=(70, 70, 64) if (light or not backdrop) else (255, 255, 255),
                  width=2)

    # readable chrome over imagery: white boxes behind title + legend
    d.rectangle([8, 6, 8 + 8 * len(title) // 1 + 16, 26], fill=(255, 255, 255))
    d.text((16, 10), title, fill=(60, 60, 55))
    legend = [("Flame", FLAME_RGB), ("Smoke", SMOKE_RGB), ("Dead tree", DEADTREE_RGB),
              ("Fallen tree", FALLEN_RGB), ("No detections", CLEAR_RGB)]
    # sized from the entries, so adding a type cannot overflow the box again
    width = 16 + sum(18 + 7 * len(label) + 22 for label, _ in legend)
    d.rectangle([8, H - 34, 8 + width, H - 8], fill=(255, 255, 255))
    x = 16
    for label, color in legend:
        d.ellipse([x, H - 27, x + 12, H - 15], fill=color, outline=(70, 70, 64))
        d.text((x + 18, H - 27), label, fill=(60, 60, 55))
        x += 18 + 7 * len(label) + 22
    if attribution:
        d.rectangle([W - 8 - 7 * len(attribution) - 12, H - 26, W - 6, H - 8],
                    fill=(255, 255, 255))
        d.text((W - 8 - 7 * len(attribution) - 6, H - 23), attribution, fill=(90, 90, 84))

    assets.mkdir(parents=True, exist_ok=True)
    dest = assets / "summary_map.png"
    img.save(dest)
    return dest


def _summary_page(batch: BatchResult, ai_text: Optional[str], assets: Path,
                  map_dir: Optional[Path] = None) -> list:
    s = batch.stats
    rows = [["Metric", "Value"]] + [
        ["Images processed", s.get("images_processed", 0)],
        ["Flagged images", s.get("flagged_images", 0)],
        ["Total detections", s.get("total_detections", 0)],
        ["Mean detections / image", s.get("mean_detections_per_image", 0)],
        ["By type", _fmt_counts(s.get("detections_by_type", {}))],
        ["Images with dead trees", s.get("images_with_deadtree", 0)],
        ["Images with fallen trees", s.get("images_with_fallen", 0)],
        ["Images with flame / smoke",
         f"{s.get('images_with_flame', 0)} / {s.get('images_with_smoke', 0)}"],
        ["Images with GPS", s.get("images_with_gps", 0)],
    ]
    st = Table([[str(c) for c in r] for r in rows], colWidths=[3.2 * inch, 5.0 * inch])
    st.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HEADER), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f9fa")]),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story = [Paragraph("Batch Summary", _H1), Spacer(1, 0.2 * inch), st, Spacer(1, 0.3 * inch)]
    map_png = _pin_map_png(batch, assets, map_dir=map_dir)
    if map_png:
        # The map gets its own page ON PURPOSE. Under the stats table it only had
        # ~2.9in of height left, and shrunk to fit it was unreadable; the page is
        # landscape, so alone it can run 9.4in wide. For a crew deciding where to
        # drive, this is the single most useful page in the report.
        story += [PageBreak(), Paragraph("Hazard Locations", _H1), Spacer(1, 0.1 * inch),
                  _fit_image(str(map_png), 9.4 * inch, 5.6 * inch)]
    story.append(PageBreak())
    story.append(Paragraph("AI Analysis & Recommendations", _H1))
    body = ai_text or ("[Automated AI analysis unavailable - LM Studio was not reachable. "
                       "Start the local LM Studio server and load the model to include analysis.]")
    body = _pdf_safe(body)
    import re as _re

    for para in body.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        # Render '## Heading' lines as section headers, light markdown-bold inline.
        first, *rest = para.split("\n")
        if first.lstrip().startswith("#"):
            story.append(Paragraph(first.lstrip("# ").strip(), _H2))
            para = "\n".join(rest).strip()
            if not para:
                continue
        para = _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", para)
        story.append(Paragraph(para.replace("\n", "<br/>"), _BODY))
        story.append(Spacer(1, 0.08 * inch))
    return story


def has_imagery(images: list[ImageResult]) -> bool:
    """Is there a single renderable frame in this run?

    False for a cloud-imported results-only run, and for one whose folders were
    deleted to reclaim disk. Both would otherwise emit pages of placeholder dashes.
    """
    for im in images:
        for p in (im.annotated_path, im.orig_display_path, im.density_path):
            if p and Path(p).exists():
                return True
    return False


def select_image_pages(batch: BatchResult, cap: int) -> tuple[list[ImageResult], Optional[str]]:
    """Pick which images carry imagery in the PDF (full page or gallery cell),
    ranked by detection count so a 250-image flight yields a readable report."""
    flagged = sorted((im for im in batch.images if im.detections),
                     key=lambda im: len(im.detections), reverse=True)
    clean = [im for im in batch.images if not im.detections]
    picked = (flagged + clean)[:max(1, cap)]
    note = None
    if len(batch.images) > len(picked):
        note = (f"Imagery shown for the top {len(picked)} of {len(batch.images)} images "
                "(ranked by detections). Every image is listed in the Image Index at "
                "the end; full imagery and every detection stay in batch.json and the "
                "review console.")
    return picked, note


def build_report(
    batch: BatchResult,
    out_path: str | Path,
    ai_text: Optional[str] = None,
    max_image_pages: int = 30,
    map_dir: Optional[Path] = None,  # offline tile cache for a real map backdrop
    branding_dir: Optional[Path] = None,  # branding/ folder -> logo + name in the PDF
    detail_pages: int = 4,  # how many of those get a FULL page; rest go in the gallery
) -> Path:
    """Build the PDF field report at `out_path`. Returns the path.

    Section order: cover (what this run is) -> summary (stats, hazard map, AI
    analysis) -> detail pages for the worst images -> gallery -> full index.
    `max_image_pages` caps how many images carry imagery at all; `detail_pages`
    splits that budget between full pages and the contact sheet.
    """
    global _BRAND
    _BRAND = _load_branding(branding_dir)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    assets = out_path.parent / "_report_assets"

    doc = SimpleDocTemplate(
        str(out_path), pagesize=PAGE,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
        topMargin=0.7 * inch, bottomMargin=0.6 * inch,
        title=_pdf_safe(f"{_BRAND.get('name') or 'Wildfire Hazardous Tree Mapping'} - Field Report"),
    )
    picked, cap_note = select_image_pages(batch, max_image_pages)
    # Reader order: what this is -> what it found -> the evidence behind it.
    # Per-image material used to sit in the middle, so a reader had to page past
    # 30 image pages to reach the numbers and the map they actually came for.
    story: list = []
    story += _cover(batch)
    note = cap_note if has_imagery(picked) else (
        "No imagery is stored for this run, so the per-image pages and gallery are "
        "omitted. Detections, GPS and statistics below are complete; every image is "
        "listed in the Image Index.")
    if note:
        story.insert(-1, Paragraph(note, _SMALL))  # before the cover's PageBreak
    story += _summary_page(batch, ai_text, assets, map_dir=map_dir)
    story.append(PageBreak())

    # Only the worst few earn a full page; the rest go on the contact sheet.
    # Both are skipped when the run holds no frames to draw.
    if has_imagery(picked):
        detail = picked[:max(0, detail_pages)]
        for im in detail:
            story += _image_page(im, assets)
        story += _gallery_pages(picked[len(detail):], assets)
    story += _image_index(batch)

    doc.build(story, onFirstPage=_banner, onLaterPages=_banner, canvasmaker=_NumberedCanvas)
    return out_path
