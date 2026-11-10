"""Channel watermark: a round logo plus optional handle text, burned into every render (clip and hook intro)."""
from pathlib import Path

import cv2
import numpy as np

from captions import _clean

FILE = Path(__file__).parent / "data" / "watermark.png"
FRAME_FILE = FILE.with_name("frame.png")  # 1080x1920 template; the video plays in its middle
MAX_UPLOAD = 5 * 1024 * 1024
POSITIONS = ("tl", "tr", "bc")
SIZE, MARGIN, TOP, BOTTOM_Y = 128, 44, 150, 1600  # px on the 1080x1920 frame; TOP clears TikTok's top tabs


def save_round_logo(data: bytes) -> None:
    """Any uploaded image -> data/watermark.png: centered square, white behind transparency, round alpha mask."""
    img = _decode(data)
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
    elif img.shape[2] == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    alpha = img[:, :, 3:4] / 255.0
    rgb = (img[:, :, :3] * alpha + 255 * (1 - alpha)).astype(np.uint8)
    h, w = rgb.shape[:2]
    side = min(h, w)
    top, left = (h - side) // 2, (w - side) // 2
    big = SIZE * 8  # draw the circle large, then shrink: smooth edge
    square = cv2.resize(rgb[top:top + side, left:left + side], (big, big), interpolation=cv2.INTER_AREA)
    mask = np.zeros((big, big), np.uint8)
    cv2.circle(mask, (big // 2, big // 2), big // 2 - 4, 255, -1)
    _write(cv2.resize(np.dstack([square, mask]), (SIZE * 2, SIZE * 2), interpolation=cv2.INTER_AREA), FILE)


def _decode(data: bytes):
    if len(data) > MAX_UPLOAD:
        raise ValueError("Gambar maksimal 5 MB")
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError("File bukan gambar yang didukung (PNG/JPG/WebP)")
    return img


def _write(img, path: Path):
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp.png")
    cv2.imwrite(str(tmp), img)
    tmp.replace(path)  # never leave a half-written image for a render running now


def save_frame(data: bytes) -> None:
    """Uploaded template -> data/frame.png at exactly 1080x1920 (scaled to cover, center-cropped, opaque)."""
    img = _decode(data)
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif img.shape[2] == 4:
        alpha = img[:, :, 3:4] / 255.0
        img = (img[:, :, :3] * alpha).astype(np.uint8)  # transparent parts become black
    h, w = img.shape[:2]
    scale = max(1080 / w, 1920 / h)
    img = cv2.resize(img, (max(1080, round(w * scale)), max(1920, round(h * scale))), interpolation=cv2.INTER_AREA)
    h, w = img.shape[:2]
    top, left = (h - 1920) // 2, (w - 1080) // 2
    _write(img[top:top + 1920, left:left + 1080], FRAME_FILE)


def active(settings: dict):
    """Watermark to burn in, from the saved settings, or None. Frame mode needs an uploaded template."""
    if settings.get("watermark_enabled") != "1":
        return None
    if settings.get("watermark_mode") == "frame" and FRAME_FILE.exists():
        return {"mode": "frame", "frame": FRAME_FILE, "logo": None, "handle": "", "position": "tl"}
    logo = FILE if FILE.exists() else None
    handle = (settings.get("watermark_handle") or "").strip()
    if not logo and not handle:
        return None
    position = settings.get("watermark_position") if settings.get("watermark_position") in POSITIONS else "tl"
    return {"mode": "badge", "logo": logo, "handle": handle, "position": position}


def logo_xy(position: str):
    return {"tl": (MARGIN, TOP), "tr": (1080 - MARGIN - SIZE, TOP), "bc": ((1080 - SIZE) // 2, BOTTOM_Y)}[position]


def handle_event(wm) -> str:
    """ASS line for the handle text next to (or in place of) the logo; needs the 'Handle' style."""
    if not wm or not wm["handle"]:
        return ""
    x, y = logo_xy(wm["position"])
    gap = SIZE + 18 if wm["logo"] else 0
    an, px, py = {
        "tl": (4, x + gap, y + SIZE // 2),
        "tr": (6, x + SIZE - gap, y + SIZE // 2),
        "bc": (8, 540, y + (SIZE + 12 if wm["logo"] else 0)),
    }[wm["position"]]
    return f"Dialogue: 1,0:00:00.00,9:59:59.00,Handle,,0,0,0,,{{\\an{an}\\pos({px},{py})}}{_clean(wm['handle'])}\n"


def overlay(base: str, logo_input: int, wm, out: str) -> str:
    """filter_complex chain putting the logo (ffmpeg input #logo_input) on [base] -> [out]."""
    x, y = logo_xy(wm["position"])
    tag = f"wm{logo_input}"
    return (f"[{logo_input}:v]scale={SIZE}:{SIZE},format=rgba,colorchannelmixer=aa=0.92[{tag}];"
            f"[{base}][{tag}]overlay={x}:{y}[{out}]")
