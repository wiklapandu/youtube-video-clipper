"""Local YouTube clipper website. Clips come from manual ranges or the viral-clips skill (via clipper.py);
work runs in background threads, one job at a time, and progress is stored in SQLite for the UI to poll."""
import json
import re
import threading
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
import pipeline
from captions import parse_time

WORK_LOCK = threading.Lock()  # whisper + ffmpeg are heavy; run one job at a time


@asynccontextmanager
async def lifespan(_):
    db.fail_interrupted_clips()
    yield


app = FastAPI(lifespan=lifespan)


def clip_or_404(clip_id: str):
    if not re.fullmatch(r"[0-9a-f]{32}", clip_id):
        raise HTTPException(400, "clip_id tidak valid")
    clip = db.get_clip(clip_id)
    if not clip:
        raise HTTPException(404, "klip tidak ditemukan")
    return clip


def prepare_job(clip_id: str):
    """Download the clip range and transcribe it -> status 'transcribed' (ready for caption editing)."""
    with WORK_LOCK:
        clip = db.get_clip(clip_id)
        if not clip:
            return  # deleted while queued
        d = pipeline.JOBS / clip_id
        d.mkdir(parents=True, exist_ok=True)
        try:
            db.set_clip(clip_id, status="downloading", message=None)
            info = pipeline.download_range(clip["url"], clip["start"], clip["end"], d)
            video = db.get_video(clip["video_id"])
            if not video["title"]:
                db.set_video(video["id"], title=info.get("title"), duration=info.get("duration"))
            db.set_clip(clip_id, status="transcribing")
            lines, language = pipeline.transcribe_lines(d / "clip.mp4")
            (d / "lines.json").write_text(json.dumps(lines))
            db.set_clip(clip_id, status="transcribed", message=f"bahasa: {language}")
        except Exception as e:
            db.set_clip(clip_id, status="error", message=str(e)[:500])


def render_job(clip_id: str, texts: list[str]):
    with WORK_LOCK:
        if not db.get_clip(clip_id):
            return
        try:
            db.set_clip(clip_id, status="rendering", message=None)
            pipeline.render_clip(pipeline.JOBS / clip_id, texts)
            db.set_clip(clip_id, status="done")
        except Exception as e:
            db.set_clip(clip_id, status="error", message=str(e)[:500])


class ManualClipIn(BaseModel):
    url: str
    start: str
    end: str


class RenderIn(BaseModel):
    texts: list[str]


@app.get("/api/videos")
def videos():
    return db.list_videos()


@app.post("/api/clips")
def add_manual_clip(body: ManualClipIn, tasks: BackgroundTasks):
    if not pipeline.is_youtube(body.url):
        raise HTTPException(400, "URL harus link YouTube")
    try:
        start, end = parse_time(body.start), parse_time(body.end)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not 0 < end - start <= pipeline.MAX_CLIP_SECONDS:
        raise HTTPException(400, f"Selesai harus setelah mulai, maksimal {pipeline.MAX_CLIP_SECONDS} detik")
    clip_id = db.add_clip(db.get_or_create_video(body.url), start, end, "manual", "queued")
    tasks.add_task(prepare_job, clip_id)
    return {"id": clip_id}


@app.post("/api/clips/{clip_id}/prepare")
def prepare(clip_id: str, tasks: BackgroundTasks):
    clip = clip_or_404(clip_id)
    if clip["status"] not in ("candidate", "error"):
        raise HTTPException(409, "Klip sedang/sudah diproses")
    db.set_clip(clip_id, status="queued", message=None)
    tasks.add_task(prepare_job, clip_id)
    return {"id": clip_id}


@app.get("/api/clips/{clip_id}/lines")
def lines(clip_id: str):
    clip_or_404(clip_id)
    path = pipeline.JOBS / clip_id / "lines.json"
    if not path.exists():
        raise HTTPException(409, "Klip belum ditranskrip")
    return json.loads(path.read_text())


@app.post("/api/clips/{clip_id}/render")
def render(clip_id: str, body: RenderIn, tasks: BackgroundTasks):
    clip = clip_or_404(clip_id)
    if not (pipeline.JOBS / clip_id / "lines.json").exists() or clip["status"] in db.BUSY:
        raise HTTPException(409, "Klip belum siap dirender")
    db.set_clip(clip_id, status="queued", message=None)
    tasks.add_task(render_job, clip_id, body.texts)
    return {"id": clip_id}


@app.delete("/api/clips/{clip_id}")
def delete(clip_id: str):
    clip = clip_or_404(clip_id)
    if clip["status"] in db.BUSY:
        raise HTTPException(409, "Klip sedang diproses")
    db.delete_clip(clip_id)
    return {"ok": True}


@app.get("/api/media/{clip_id}/{name}")
def media(clip_id: str, name: str, download: bool = False):
    if name not in {"clip.mp4", "final.mp4"}:
        raise HTTPException(404)
    clip_or_404(clip_id)
    path = pipeline.JOBS / clip_id / name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="video/mp4", filename=f"clip-{clip_id[:8]}.mp4" if download else None)


app.mount("/", StaticFiles(directory=pipeline.ROOT / "static", html=True), name="static")
