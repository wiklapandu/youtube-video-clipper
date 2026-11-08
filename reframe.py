"""Face-following 9:16 crop: detect faces, smooth a camera path, drive ffmpeg's crop via sendcmd.

shortcut: Haar detector (bundled with OpenCV 4) misses side-facing faces and picks the largest face,
not the active speaker; switch to YuNet/mediapipe if tracking quality is not enough.
"""
import subprocess
from pathlib import Path

import cv2
import numpy as np

SAMPLE_FPS = 5
DETECT_H = 360


def crop_width(iw: int, ih: int) -> int:
    return min(iw, int(ih * 9 / 16) // 2 * 2)


def plan_track(centers, frame_w, crop_w, alpha=0.25, deadband=0.04):
    """centers: face center x (source px) or None per sample -> smoothed crop x per sample.
    Missing faces keep the last known position; no face at all stays centered."""
    max_x = frame_w - crop_w
    known = [c for c in centers if c is not None]
    if not known:
        return [max_x // 2] * len(centers)
    last, cur, xs = known[0], None, []
    for c in centers:
        if c is not None:
            last = c
        target = min(max(last - crop_w / 2, 0), max_x)
        if cur is None:
            cur = target
        elif abs(target - cur) > deadband * crop_w:  # ignore detector jitter
            cur += alpha * (target - cur)
        xs.append(int(round(cur)))
    return xs


def sendcmd_script(xs, fps, n_frames):
    """One crop x command per output frame where the (even) x changes, interpolated between samples."""
    times = np.arange(len(xs)) / SAMPLE_FPS
    lines, prev = [], None
    for i in range(n_frames):
        t = i / fps
        x = int(np.interp(t, times, xs)) // 2 * 2
        if x != prev:
            lines.append(f"{t:.3f} crop@rf x {x};")
            prev = x
    return "\n".join(lines) + "\n"


def detect_centers(clip: Path, ffmpeg: str, iw: int, ih: int):
    sw = round(iw * DETECT_H / ih / 2) * 2
    raw = subprocess.run(
        [ffmpeg, "-loglevel", "error", "-i", str(clip),
         "-vf", f"fps={SAMPLE_FPS},scale={sw}:{DETECT_H},format=gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True,
    ).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, DETECT_H, sw)
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    centers = []
    for f in frames:
        faces = cascade.detectMultiScale(f, scaleFactor=1.1, minNeighbors=6, minSize=(DETECT_H // 10,) * 2)
        if len(faces):
            x, _, w, _ = max(faces, key=lambda r: r[2] * r[3])
            centers.append((x + w / 2) * iw / sw)
        else:
            centers.append(None)
    return centers


def crop_filter(clip: Path, ffmpeg: str, job_dir: Path) -> str:
    """ffmpeg filter that crops a 9:16 window following the main face (writes reframe.cmd in job_dir)."""
    cap = cv2.VideoCapture(str(clip))
    iw, ih = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    cw = crop_width(iw, ih)
    if cw >= iw:  # already vertical: nothing to follow
        return "null"
    xs = plan_track(detect_centers(clip, ffmpeg, iw, ih), iw, cw)
    if not xs:
        return f"crop={cw}:{ih}"
    n_frames = n_frames or int(len(xs) / SAMPLE_FPS * fps)
    (job_dir / "reframe.cmd").write_text(sendcmd_script(xs, fps, n_frames))
    return f"sendcmd=f=reframe.cmd,crop@rf=w={cw}:h={ih}:x={xs[0]}:y=0"
