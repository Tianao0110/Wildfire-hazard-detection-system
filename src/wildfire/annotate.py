"""Drawing: clustered, labeled detection boxes and a grid density (hazard-count) map.

Per field feedback, the annotated image clusters nearby detections into a single
bold, labeled box (type + count + confidence) instead of many tiny faint boxes.
Colors: flame = red, smoke = dark slate, dead tree = bone white. All functions
operate on BGR uint8 images and return new arrays.
"""

from __future__ import annotations

from collections import Counter, defaultdict

import cv2
import numpy as np

from .types import Detection

# BGR colors (OpenCV). Flame = red, Smoke = dark slate, Dead tree = bone white.
# Smoke and dead tree used to be orange and yellow, only 1.48:1 apart and
# indistinguishable to red-green colorblind eyes; slate vs bone is 3.42:1.
# Keep in sync with KIND in console/static/console.js and :root in console.css.
# Fallen tree is sky blue #29B6F6. No colour can reach 3:1 luminance contrast
# against all three of the others - slate is dark and bone is light, and the
# band between them is too narrow from both sides - so it is separated by HUE:
# under simulated deuteranopia and protanopia its smallest distance to any of
# them is dE 40+, wider than the existing flame/bone pair (30.1). It also has
# its own icon (a log lying diagonally), so colour is never the only channel.
DEADTREE_COLOR = (176, 205, 217)  # #D9CDB0
FALLEN_COLOR = (246, 182, 41)     # #29B6F6
FLAME_COLOR = (85, 85, 224)       # #E05555
SMOKE_COLOR = (122, 110, 84)      # #546E7A
OTHER_COLOR = (0, 255, 0)

COLOR_BY_DISPLAY = {
    "Dead Tree": DEADTREE_COLOR,
    "Fallen Tree": FALLEN_COLOR,
    "Flame": FLAME_COLOR,
    "Smoke": SMOKE_COLOR,
}

_FONT = cv2.FONT_HERSHEY_SIMPLEX


def _color_for(display: str) -> tuple[int, int, int]:
    return COLOR_BY_DISPLAY.get(display, OTHER_COLOR)


def _text_color(box_color: tuple[int, int, int]) -> tuple[int, int, int]:
    """Black text on light boxes (bone), white on dark (red/slate)."""
    b, g, r = box_color
    luminance = 0.114 * b + 0.587 * g + 0.299 * r
    return (0, 0, 0) if luminance > 140 else (255, 255, 255)


def _clip_box(xyxy, W, H) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = (int(round(v)) for v in xyxy)
    x1, x2 = max(0, min(x1, W - 1)), max(0, min(x2, W - 1))
    y1, y2 = max(0, min(y1, H - 1)), max(0, min(y2, H - 1))
    return x1, y1, x2, y2


def cluster_detections(detections: list[Detection], H: int, W: int, gap: int) -> list[dict]:
    """Group nearby detections into merged clusters.

    Boxes are rasterized to a mask, dilated by ``gap`` so neighbours touch, and
    connected components are found. Each cluster's box is the tight union of its
    members' boxes (so the whole subject is enclosed). Returns dicts with
    ``xyxy``, ``count``, ``score`` (max member confidence), and ``display``
    (dominant type).
    """
    if not detections:
        return []
    mask = np.zeros((H, W), np.uint8)
    for d in detections:
        x1, y1, x2, y2 = _clip_box(d.xyxy, W, H)
        if x2 > x1 and y2 > y1:
            mask[y1:y2, x1:x2] = 255
    if gap > 0:
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (gap, gap))
        mask = cv2.dilate(mask, k)
    _, labels = cv2.connectedComponents(mask, connectivity=8)

    groups: dict[int, list[Detection]] = defaultdict(list)
    for d in detections:
        cx = int(np.clip((d.xyxy[0] + d.xyxy[2]) / 2, 0, W - 1))
        cy = int(np.clip((d.xyxy[1] + d.xyxy[3]) / 2, 0, H - 1))
        lab = int(labels[cy, cx])
        if lab != 0:
            groups[lab].append(d)

    clusters = []
    for ds in groups.values():
        clusters.append({
            "xyxy": (
                min(d.xyxy[0] for d in ds), min(d.xyxy[1] for d in ds),
                max(d.xyxy[2] for d in ds), max(d.xyxy[3] for d in ds),
            ),
            "count": len(ds),
            "score": max(d.score for d in ds),
            "display": Counter(d.display for d in ds).most_common(1)[0][0],
        })
    return clusters


def draw_boxes(
    image_bgr: np.ndarray,
    detections: list[Detection],
    cluster: bool = True,
    cluster_gap: int | None = None,
    show_labels: bool = True,
) -> np.ndarray:
    """Draw bold, labeled, color-coded boxes; nearby detections are merged.

    Each box gets a dark underlay for contrast on any background, plus a label
    "<Type> x<count>  <conf>%" (count omitted when 1).
    """
    out = image_bgr.copy()
    H, W = out.shape[:2]
    scale = min(H, W)
    thick = max(3, round(scale / 450))
    font_scale = max(0.6, scale / 2400)
    font_th = max(2, round(scale / 2000))

    if cluster:
        gap = cluster_gap if cluster_gap is not None else max(10, scale // 50)
        items = cluster_detections(detections, H, W, gap)
    else:
        items = [
            {"xyxy": d.xyxy, "count": 1, "score": d.score, "display": d.display}
            for d in detections
        ]

    for it in items:
        x1, y1, x2, y2 = _clip_box(it["xyxy"], W, H)
        color = _color_for(it["display"])
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 0, 0), thick + 3)  # dark underlay
        cv2.rectangle(out, (x1, y1), (x2, y2), color, thick)

        if show_labels:
            cnt = it["count"]
            pct = f"{it['score'] * 100:.0f}%"
            label = f"{it['display']} x{cnt}  {pct}" if cnt > 1 else f"{it['display']}  {pct}"
            (tw, th), base = cv2.getTextSize(label, _FONT, font_scale, font_th)
            top = y1 - th - base - 4
            if top < 0:  # not enough room above -> put label inside the top edge
                top = y1 + 2
            cv2.rectangle(out, (x1, top), (x1 + tw + 8, top + th + base + 4), color, -1)
            cv2.putText(
                out, label, (x1 + 4, top + th + 2),
                _FONT, font_scale, _text_color(color), font_th, cv2.LINE_AA,
            )
    return out


def highlight_detections(
    image_bgr: np.ndarray,
    detections: list[Detection],
    alpha: float = 0.45,
) -> np.ndarray:
    """Paint each detection: a translucent color-filled ellipse over the crown/region
    (dead tree=yellow, flame=red, smoke=orange) with a thin dark outline.

    This "colors the tree" directly (per field preference) instead of drawing boxes.
    """
    H, W = image_bgr.shape[:2]
    overlay = image_bgr.copy()
    geoms = []
    for d in detections:
        x1, y1, x2, y2 = _clip_box(d.xyxy, W, H)
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        ax, ay = max(3, (x2 - x1) // 2), max(3, (y2 - y1) // 2)
        geoms.append((cx, cy, ax, ay))
        cv2.ellipse(overlay, (cx, cy), (ax, ay), 0, 0, 360, _color_for(d.display), -1)
    out = cv2.addWeighted(overlay, alpha, image_bgr, 1 - alpha, 0)

    outline = max(2, round(min(H, W) / 900))
    for (cx, cy, ax, ay), d in zip(geoms, detections):
        cv2.ellipse(out, (cx, cy), (ax, ay), 0, 0, 360, (0, 0, 0), outline)
    return out


def _count_ramp(f: float) -> tuple[int, int, int]:
    """Map a 0..1 density to a BGR color: green (low) -> yellow -> red (high)."""
    f = float(np.clip(f, 0.0, 1.0))
    if f < 0.5:
        return (0, 255, int(2 * f * 255))  # green -> yellow
    return (0, int((1 - (f - 0.5) * 2) * 255), 255)  # yellow -> red


def grid_density_map(
    image_bgr: np.ndarray,
    detections: list[Detection],
    rows: int = 6,
    cols: int = 8,
    alpha: float = 0.6,
) -> np.ndarray:
    """Grid statistics overlay: split the image into a rows x cols grid, count
    detections per cell, shade cells green->red by count, and print the count.

    Gives a quantifiable view of where hazards concentrate (high-count cells =
    high-risk zones) rather than a per-detection blob.
    """
    H, W = image_bgr.shape[:2]
    rows = max(1, rows)
    cols = max(1, cols)
    counts = np.zeros((rows, cols), dtype=int)
    for d in detections:
        cx = (d.xyxy[0] + d.xyxy[2]) / 2
        cy = (d.xyxy[1] + d.xyxy[3]) / 2
        c = min(cols - 1, max(0, int(cx / (W / cols))))
        r = min(rows - 1, max(0, int(cy / (H / rows))))
        counts[r, c] += 1
    maxc = int(counts.max())

    def cell_box(r, c):
        return int(c * W / cols), int(r * H / rows), int((c + 1) * W / cols), int((r + 1) * H / rows)

    # shade each cell with opacity scaled by its count: faint for low counts so
    # the imagery shows through, bold for hotspots.
    out = image_bgr.copy()
    for r in range(rows):
        for c in range(cols):
            cnt = counts[r, c]
            if cnt > 0 and maxc > 0:
                f = cnt / maxc
                a = alpha * (f ** 1.3)  # low counts ~transparent, hotspots bold
                x1, y1, x2, y2 = cell_box(r, c)
                roi = out[y1:y2, x1:x2].astype(np.float32)
                color = np.array(_count_ramp(f), np.float32)
                out[y1:y2, x1:x2] = (roi * (1 - a) + color * a).astype(np.uint8)

    # crisp grid lines + per-cell counts
    line = max(1, round(min(H, W) / 600))
    font_scale = max(0.6, min(H, W) / 1500)
    font_th = max(2, round(min(H, W) / 1300))
    for r in range(rows):
        for c in range(cols):
            x1, y1, x2, y2 = cell_box(r, c)
            cv2.rectangle(out, (x1, y1), (x2, y2), (255, 255, 255), line)
            cnt = int(counts[r, c])
            if cnt > 0:
                txt = str(cnt)
                (tw, th), _ = cv2.getTextSize(txt, _FONT, font_scale, font_th)
                tx, ty = x1 + (x2 - x1 - tw) // 2, y1 + (y2 - y1 + th) // 2
                cv2.putText(out, txt, (tx, ty), _FONT, font_scale, (0, 0, 0), font_th + 2, cv2.LINE_AA)
                cv2.putText(out, txt, (tx, ty), _FONT, font_scale, (255, 255, 255), font_th, cv2.LINE_AA)
    return out
