"""Media work shared by the website and the CLI: download, transcribe, render."""
import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import yt_dlp
from yt_dlp.utils import download_range_func

from captions import group_words, retime, to_ass
from reframe import crop_filter

ROOT = Path(__file__).parent
JOBS = ROOT / "jobs"
MAX_CLIP_SECONDS = 180
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
# Plain Homebrew ffmpeg has no libass; ffmpeg-full does.
FFMPEG = os.environ.get("FFMPEG") or next(
    (p for p in ["/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"] if Path(p).exists()), shutil.which("ffmpeg") or "ffmpeg"
)
YDL_BASE = {"ffmpeg_location": FFMPEG, "noplaylist": True, "quiet": True, "no_warnings": True}

_model = None


def is_youtube(url: str) -> bool:
    return urlparse(url).hostname in YOUTUBE_HOSTS


def whisper():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        _model = WhisperModel(os.environ.get("WHISPER_MODEL", "small"), device="cpu", compute_type="int8")
    return _model


def decode_audio(path: Path):
    # Decode with ffmpeg ourselves: faster-whisper's PyAV decoder breaks on av>=17.
    pcm = subprocess.run([FFMPEG, "-loglevel", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(pcm, dtype=np.float32)


def download_range(url: str, start: float, end: float, d: Path) -> dict:
    """Download only [start, end] of the video to d/clip.mp4. Returns yt-dlp info (title, duration...)."""
    opts = {
        **YDL_BASE,
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
        "outtmpl": str(d / "clip.%(ext)s"),
        "merge_output_format": "mp4",
        "download_ranges": download_range_func(None, [(start, end)]),
        "force_keyframes_at_cuts": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    if not (d / "clip.mp4").exists():
        raise RuntimeError("file video tidak terbentuk")
    return info


def transcribe_lines(clip: Path):
    """Whisper with word timestamps -> (caption lines, language)."""
    segments, info = whisper().transcribe(decode_audio(clip), word_timestamps=True, vad_filter=True)
    words = [
        {"word": w.word.strip(), "start": w.start, "end": w.end}
        for s in segments
        for w in (s.words or [])
        if w.word.strip()
    ]
    return group_words(words), info.language


def render_clip(d: Path, texts: list[str]):
    """Burn edited captions into a face-following 9:16 crop: d/clip.mp4 + d/lines.json -> d/final.mp4."""
    original = json.loads((d / "lines.json").read_text())
    if len(texts) != len(original):
        raise ValueError("Jumlah baris caption tidak cocok")
    lines = [l for l in (retime(o, t) for o, t in zip(original, texts)) if l]
    (d / "captions.ass").write_text(to_ass(lines), encoding="utf-8")
    vf = (
        f"{crop_filter(d / 'clip.mp4', FFMPEG, d)},"
        "scale=1080:1920:force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,"
        "ass=captions.ass"
    )
    cmd = [FFMPEG, "-y", "-loglevel", "error", "-i", "clip.mp4", "-vf", vf,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k",
           "-movflags", "+faststart", "final.mp4"]
    # cwd=job dir so the ass/sendcmd filter paths need no escaping
    result = subprocess.run(cmd, cwd=d, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg gagal: {result.stderr[-500:]}")


# ---------- full-video transcript for finding viral moments ----------

def pick_subtitle_url(info: dict):
    """Best json3 subtitle: manual in the video language, then YouTube's original-language auto captions,
    then any manual track. None when nothing usable."""
    subs, auto = info.get("subtitles") or {}, info.get("automatic_captions") or {}
    lang = info.get("language")
    candidates = [subs.get(lang)] if lang else []
    candidates += [auto[k] for k in auto if k.endswith("-orig")]
    candidates += list(subs.values())
    for formats in candidates:
        for f in formats or []:
            if f.get("ext") == "json3":
                return f["url"]
    return None


def json3_chunks(events, max_len=8.0):
    """YouTube json3 caption events -> [(start_seconds, text)] chunks of at most ~max_len seconds."""
    chunks, start, words = [], None, []
    for ev in events:
        text = "".join(s.get("utf8", "") for s in ev.get("segs") or []).replace("\n", " ").strip()
        if not text:
            continue
        t = ev["tStartMs"] / 1000
        if start is not None and (t - start > max_len or words[-1].endswith((".", "?", "!"))):
            chunks.append((start, " ".join(words)))
            start, words = None, []
        if start is None:
            start = t
        words.append(text)
    if words:
        chunks.append((start, " ".join(words)))
    return chunks


def format_transcript(chunks) -> str:
    return "\n".join(f"[{int(t // 60)}:{int(t % 60):02d}] {text}" for t, text in chunks)


def video_transcript(url: str, d: Path):
    """Timestamped transcript of the whole video -> (info, source). Writes d/transcript.txt.
    YouTube captions when available (seconds), else Whisper on the full audio (slow)."""
    with yt_dlp.YoutubeDL({**YDL_BASE, "skip_download": True}) as ydl:
        info = ydl.extract_info(url, download=False)
        sub_url = pick_subtitle_url(info)
        chunks = json3_chunks(json.loads(ydl.urlopen(sub_url).read())["events"]) if sub_url else []
    source = "youtube-captions"
    if not chunks:
        source = "whisper"
        with yt_dlp.YoutubeDL({**YDL_BASE, "format": "ba/b", "outtmpl": str(d / "audio.%(ext)s")}) as ydl:
            path = Path(ydl.prepare_filename(ydl.extract_info(url, download=True)))
        segments, _ = whisper().transcribe(decode_audio(path), vad_filter=True)
        chunks = [(s.start, s.text.strip()) for s in segments if s.text.strip()]
        path.unlink(missing_ok=True)
    (d / "transcript.txt").write_text(format_transcript(chunks), encoding="utf-8")
    return info, source
