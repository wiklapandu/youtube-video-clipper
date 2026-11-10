"""Media work shared by the website and the CLI: download, transcribe, render."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import yt_dlp
from yt_dlp.utils import download_range_func

import compose
import reframe
import watermark
from captions import group_words, hook_ass, retime, to_ass

ROOT = Path(__file__).parent
JOBS = ROOT / "jobs"
INSERTS = JOBS / "inserts"  # extra YouTube videos, one folder per insert
OVERLAY_MODES = ("split", "cutaway", "pip")
MAX_CLIP_SECONDS = 180
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
# Plain Homebrew ffmpeg has no libass; ffmpeg-full does.
FFMPEG = os.environ.get("FFMPEG") or next(
    (p for p in ["/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"] if Path(p).exists()), shutil.which("ffmpeg") or "ffmpeg"
)
_probe = Path(FFMPEG).with_name("ffprobe")
FFPROBE = str(_probe) if _probe.exists() else "ffprobe"
HOOK_VOICES = {"ardi": "id-ID-ArdiNeural", "gadis": "id-ID-GadisNeural"}  # free Edge "Read aloud" voices
MAC_VOICE = os.environ.get("HOOK_MAC_VOICE", "Damayanti")  # offline fallback (macOS `say`)
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


def _run(cmd, cwd):
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{Path(cmd[0]).name} gagal: {result.stderr[-500:]}")


def _probe(path: Path, entry: str) -> str:
    return subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", entry, "-of", "csv=p=0", str(path)],
                          capture_output=True, text=True, check=True).stdout.strip().split("\n")[0]


ENCODE = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"]


def _duration(path: Path) -> float:
    return float(subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                                capture_output=True, text=True, check=True).stdout)


def has_audio(path: Path) -> bool:
    return bool(subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                                "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip())


def encode_segment(d: Path, main: Path, out: str, *, info: dict, follow: str, ass=None, overlays=(), wm=None):
    """One 1080x1920 segment from `main` plus overlay videos, captions and watermark/frame -> d/out.
    Audio comes from `main` only (overlays are muted); silence when it has none."""
    fps = _probe(main, "stream=r_frame_rate") or "30"
    args = ["-i", str(main)]
    for o in overlays:
        args += ["-i", str(o["file"])]
    k = 1 + len(overlays)
    logo_in = frame_in = None
    if wm and wm.get("frame"):
        args += ["-loop", "1", "-framerate", fps, "-i", str(wm["frame"])]
        frame_in, k = k, k + 1
    elif wm and wm.get("logo"):
        args += ["-i", str(wm["logo"])]
        logo_in, k = k, k + 1
    graph = compose.segment_graph(
        follow=follow, crop_for=lambda aspect: reframe.static_crop(info, aspect), fps=fps,
        overlays=[{**o, "input": 1 + i} for i, o in enumerate(overlays)],
        ass=ass, wm=wm, logo_input=logo_in, frame_input=frame_in)
    if has_audio(main):
        audio = ["-map", "0:a"]
    else:
        args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        audio = ["-map", f"{k}:a", "-shortest"]
    # cwd=job dir so the ass/sendcmd filter paths need no escaping
    _run([FFMPEG, "-y", "-loglevel", "error", *args, "-filter_complex", graph, "-map", "[v]", *audio, *ENCODE, out], d)


def render_clip(d: Path, texts: list[str], hook: str | None = None, voice: str = "ardi", wm=None,
                inserts=()) -> str | None:
    """d/clip.mp4 + d/lines.json -> d/final.mp4: edited karaoke captions, face-following 9:16 crop (or the
    frame template), extra videos (inserts: dicts with file, mode, at, src_start, src_end, id), and an
    optional voiced hook intro. Returns a note for the user, if any."""
    original = json.loads((d / "lines.json").read_text())
    if len(texts) != len(original):
        raise ValueError("Jumlah baris caption tidak cocok")
    lines = [l for l in (retime(o, t) for o, t in zip(original, texts)) if l]
    handle = watermark.handle_event(wm)
    (d / "captions.ass").write_text(to_ass(lines) + handle, encoding="utf-8")

    clip_len, notes, overlays = _duration(d / "clip.mp4"), [], []
    for ins in inserts:
        if ins["mode"] not in OVERLAY_MODES:
            continue
        at = ins["at"] or 0
        if at >= clip_len - 0.2:
            notes.append(f"Video tambahan di detik {at:g} dilewati karena melewati durasi klip")
            continue
        overlays.append({"file": ins["file"], "mode": ins["mode"], "at": at,
                         "dur": min(ins["src_end"] - ins["src_start"], clip_len - at)})
    info = reframe.analyze(d / "clip.mp4", FFMPEG)
    encode_segment(d, d / "clip.mp4", "body.mp4", info=info, follow=reframe.follow_crop(info, d),
                   ass="captions.ass", overlays=overlays, wm=wm)

    def joined(mode):  # extra videos glued before/after the clip, branded the same way
        names = []
        for ins in (i for i in inserts if i["mode"] == mode):
            ass = None
            if handle:
                ass = f"seg-{ins['id']}.ass"
                (d / ass).write_text(to_ass([]) + handle, encoding="utf-8")
            seg_info = reframe.probe(ins["file"])
            encode_segment(d, ins["file"], f"seg-{ins['id']}.mp4", info=seg_info,
                           follow=reframe.static_crop(seg_info, 9 / 16), ass=ass, wm=wm)
            names.append(f"seg-{ins['id']}.mp4")
        return names

    segments = joined("before") + ["body.mp4"] + joined("after")
    if hook:
        intro, note = make_intro(d, hook, voice, wm)
        segments.insert(0, intro)
        notes.append(note)
    if segments == ["body.mp4"]:
        (d / "body.mp4").replace(d / "final.mp4")
    else:
        concat_segments(d, segments)
    return "; ".join(n for n in notes if n) or None


def concat_segments(d: Path, names: list[str]):
    """Join segments (all 1080x1920) at the main clip's frame rate -> d/final.mp4."""
    fps = _probe(d / "body.mp4", "stream=r_frame_rate") or "30"
    audio = "aresample=48000,aformat=channel_layouts=stereo"
    graph = "".join(f"[{i}:v]fps={fps},scale=1080:1920,format=yuv420p,setsar=1[v{i}];[{i}:a]{audio}[a{i}];"
                    for i in range(len(names)))
    graph += "".join(f"[v{i}][a{i}]" for i in range(len(names))) + f"concat=n={len(names)}:v=1:a=1[v][a]"
    inputs = [arg for name in names for arg in ("-i", name)]
    _run([FFMPEG, "-y", "-loglevel", "error", *inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
          *ENCODE, "final.mp4"], d)


def speak(d: Path, text: str, voice: str):
    """Hook voice-over -> (audio file name in d, note). Edge neural voice; macOS `say` when Edge is unreachable."""
    try:
        import edge_tts

        edge_tts.Communicate(text, HOOK_VOICES[voice]).save_sync(str(d / "hook.mp3"))
        return "hook.mp3", None
    except Exception as e:  # offline, or the unofficial Edge service changed
        (d / "hook.txt").write_text(text, encoding="utf-8")
        _run(["say", "-v", MAC_VOICE, "-f", "hook.txt", "-o", "hook.aiff"], d)  # -f: text can't be read as a flag
        return "hook.aiff", f"Suara Edge gagal ({type(e).__name__}), hook memakai suara Mac"


def speech_end(audio: Path) -> float:
    """Seconds until the last spoken sound (TTS files end with up to ~1 s of silence)."""
    log = subprocess.run([FFMPEG, "-i", str(audio), "-af", "silencedetect=n=-45dB:d=0.25", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    total = float(subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(audio)],
                                 capture_output=True, text=True, check=True).stdout)
    starts = re.findall(r"silence_start: ([\d.]+)", log)
    ends = re.findall(r"silence_end: ([\d.]+)", log)
    if starts and (len(ends) < len(starts) or float(ends[-1]) >= total - 0.05):  # trailing silence runs to the end
        return float(starts[-1])
    return total


def make_intro(d: Path, hook: str, voice: str = "ardi", wm=None):
    """Hook intro segment from d/body.mp4's first frame: blurred, hook title, voice-over -> ("intro.mp4", note)."""
    # quotes only mark the yellow highlight; read aloud they make the voice pause
    audio_file, note = speak(d, re.sub(r"['\"“”‘’]", "", hook), voice)
    dur = max(2.0, speech_end(d / audio_file) + 0.6)
    (d / "hook.ass").write_text(hook_ass(hook, dur) + watermark.handle_event(wm), encoding="utf-8")
    _run([FFMPEG, "-y", "-loglevel", "error", "-i", "body.mp4", "-frames:v", "1", "hook.png"], d)
    fps = _probe(d / "body.mp4", "stream=r_frame_rate") or "30"
    logo = bool(wm and wm.get("logo"))
    video = f"[0:v]boxblur=40:2,eq=brightness=-0.18,fps={fps},format=yuv420p,ass=hook.ass"
    if logo:  # sharp logo on top of the blurred one baked into body.mp4's first frame
        video = f"{video}[ib];" + watermark.overlay("ib", 2, wm, "io") + ";[io]setsar=1,format=yuv420p[v]"
    else:
        video = f"{video},setsar=1[v]"
    graph = f"{video};[1:a]apad,atrim=0:{dur:.2f},aresample=48000,aformat=channel_layouts=stereo[a]"
    _run([FFMPEG, "-y", "-loglevel", "error", "-loop", "1", "-t", f"{dur:.2f}", "-i", "hook.png", "-i", audio_file,
          *(["-i", str(wm["logo"])] if logo else []),
          "-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-t", f"{dur:.2f}", *ENCODE, "intro.mp4"], d)
    return "intro.mp4", note


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
