"""Local YouTube clipper website. Clips come from manual ranges or the viral-clips skill (via clipper.py);
work runs in background threads, one job at a time, and progress is stored in SQLite for the UI to poll."""
import json
import re
import shutil
import threading
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db
import pipeline
import watermark
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
        shutil.rmtree(d, ignore_errors=True)  # reprocess starts clean; yt-dlp skips files that already exist
        d.mkdir(parents=True)
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


def render_job(clip_id: str, texts: list[str], hook: str | None, voice: str):
    with WORK_LOCK:
        if not db.get_clip(clip_id):
            return
        try:
            db.set_clip(clip_id, status="rendering", message=None)
            wm = watermark.active(db.get_settings())  # read at render time: latest logo/handle/frame
            inserts = [{**i, "file": pipeline.INSERTS / i["id"] / "clip.mp4"} for i in db.clip_inserts(clip_id)]
            note = pipeline.render_clip(pipeline.JOBS / clip_id, texts, hook, voice, wm, inserts)
            db.set_clip(clip_id, status="done", message=note)
        except Exception as e:
            db.set_clip(clip_id, status="error", message=str(e)[:500])


def insert_job(insert_id: str):
    """Download the chosen range of an extra YouTube video."""
    with WORK_LOCK:
        ins = db.get_insert(insert_id)
        if not ins:
            return
        d = pipeline.INSERTS / insert_id
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        try:
            db.set_insert(insert_id, status="downloading", message=None)
            info = pipeline.download_range(ins["url"], ins["src_start"], ins["src_end"], d)
            db.set_insert(insert_id, status="ready", message=info.get("title"))
        except Exception as e:
            db.set_insert(insert_id, status="error", message=str(e)[:500])


def parse_range(start: str, end: str, limit: float):
    try:
        s, e = parse_time(start), parse_time(end)
    except ValueError as err:
        raise HTTPException(400, str(err))
    if not 0 < e - s <= limit:
        raise HTTPException(400, f"Selesai harus setelah mulai, maksimal {limit:g} detik")
    return s, e


class ManualClipIn(BaseModel):
    url: str
    start: str
    end: str


class RangeIn(BaseModel):
    start: str
    end: str


class InsertIn(BaseModel):
    url: str
    start: str
    end: str
    mode: Literal["split", "cutaway", "pip", "before", "after"]
    at: str | None = None  # when it appears in the clip (overlay modes)


MAX_INSERT_SECONDS = 60


class RenderIn(BaseModel):
    texts: list[str]
    hook: str | None = Field(None, max_length=200)
    voice: Literal["ardi", "gadis"] = "ardi"


@app.get("/api/videos")
def videos():
    return db.list_videos()


@app.post("/api/clips")
def add_manual_clip(body: ManualClipIn, tasks: BackgroundTasks):
    if not pipeline.is_youtube(body.url):
        raise HTTPException(400, "URL harus link YouTube")
    start, end = parse_range(body.start, body.end, pipeline.MAX_CLIP_SECONDS)
    clip_id = db.add_clip(db.get_or_create_video(body.url), start, end, "manual", "queued")
    tasks.add_task(prepare_job, clip_id)
    return {"id": clip_id}


@app.post("/api/clips/{clip_id}/prepare")
def prepare(clip_id: str, tasks: BackgroundTasks):
    clip = clip_or_404(clip_id)
    if clip["status"] in db.BUSY:
        raise HTTPException(409, "Klip sedang diproses")
    db.set_clip(clip_id, status="queued", message=None)
    tasks.add_task(prepare_job, clip_id)
    return {"id": clip_id}


@app.patch("/api/clips/{clip_id}")
def change_range(clip_id: str, body: RangeIn, tasks: BackgroundTasks):
    """New start/end: the clip is downloaded and transcribed again."""
    clip = clip_or_404(clip_id)
    if clip["status"] in db.BUSY:
        raise HTTPException(409, "Klip sedang diproses")
    start, end = parse_range(body.start, body.end, pipeline.MAX_CLIP_SECONDS)
    duration = db.get_video(clip["video_id"])["duration"]
    if duration and end > duration + 1:
        raise HTTPException(400, f"Selesai melewati durasi video ({int(duration // 60)}:{int(duration % 60):02d})")
    db.set_clip(clip_id, start=start, end=end, status="queued", message=None)
    tasks.add_task(prepare_job, clip_id)
    return {"id": clip_id}


@app.post("/api/clips/{clip_id}/inserts")
def add_insert(clip_id: str, body: InsertIn, tasks: BackgroundTasks):
    clip = clip_or_404(clip_id)
    if not pipeline.is_youtube(body.url):
        raise HTTPException(400, "URL harus link YouTube")
    start, end = parse_range(body.start, body.end, MAX_INSERT_SECONDS)
    at = None
    if body.mode in pipeline.OVERLAY_MODES:
        try:
            at = parse_time(body.at or "0")
        except ValueError as err:
            raise HTTPException(400, str(err))
        if at >= clip["end"] - clip["start"]:
            raise HTTPException(400, "Waktu muncul melewati durasi klip")
    insert_id = db.add_insert(clip_id, body.url, start, end, body.mode, at)
    tasks.add_task(insert_job, insert_id)
    return {"id": insert_id}


@app.delete("/api/inserts/{insert_id}")
def delete_insert(insert_id: str):
    ins = db.get_insert(insert_id) if re.fullmatch(r"[0-9a-f]{32}", insert_id) else None
    if not ins:
        raise HTTPException(404, "video tambahan tidak ditemukan")
    if ins["status"] in db.BUSY:
        raise HTTPException(409, "Video tambahan sedang didownload")
    db.delete_insert(insert_id)
    shutil.rmtree(pipeline.INSERTS / insert_id, ignore_errors=True)
    return {"ok": True}


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
    if any(i["status"] != "ready" for i in db.clip_inserts(clip_id)):
        raise HTTPException(409, "Video tambahan belum siap (tunggu download selesai atau hapus yang error)")
    hook = (body.hook or "").strip() or None
    # keep the last hook text when rendering without one, so it is still there next time
    db.set_clip(clip_id, status="queued", message=None, **({"hook": hook} if hook else {}))
    tasks.add_task(render_job, clip_id, body.texts, hook, body.voice)
    return {"id": clip_id}


@app.delete("/api/clips/{clip_id}")
def delete(clip_id: str):
    clip = clip_or_404(clip_id)
    if clip["status"] in db.BUSY:
        raise HTTPException(409, "Klip sedang diproses")
    for ins in db.clip_inserts(clip_id):
        shutil.rmtree(pipeline.INSERTS / ins["id"], ignore_errors=True)
    db.delete_clip(clip_id)
    return {"ok": True}


class SettingsIn(BaseModel):
    watermark_enabled: bool
    watermark_handle: str = Field("", max_length=40)
    watermark_position: Literal["tl", "tr", "bc"] = "tl"
    watermark_mode: Literal["badge", "frame"] = "badge"


def settings_out():
    s = db.get_settings()
    has_logo, has_frame = watermark.FILE.exists(), watermark.FRAME_FILE.exists()
    return {
        "watermark_mode": s["watermark_mode"],
        "watermark_frame": f"/api/frame.png?v={int(watermark.FRAME_FILE.stat().st_mtime)}" if has_frame else None,
        "watermark_enabled": s["watermark_enabled"] == "1",
        "watermark_handle": s["watermark_handle"],
        "watermark_position": s["watermark_position"],
        "watermark_logo": f"/api/watermark.png?v={int(watermark.FILE.stat().st_mtime)}" if has_logo else None,
    }


@app.get("/api/settings")
def get_settings():
    return settings_out()


@app.put("/api/settings")
def put_settings(body: SettingsIn):
    db.set_settings(watermark_enabled="1" if body.watermark_enabled else "0",
                    watermark_handle=body.watermark_handle.strip(), watermark_position=body.watermark_position,
                    watermark_mode=body.watermark_mode)
    return settings_out()


async def save_upload(request: Request, save):
    """Raw image bytes in the body (no multipart needed)."""
    if int(request.headers.get("content-length") or 0) > watermark.MAX_UPLOAD:
        raise HTTPException(413, "Gambar maksimal 5 MB")
    try:
        save(await request.body())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return settings_out()


@app.post("/api/watermark")
async def upload_watermark(request: Request):
    return await save_upload(request, watermark.save_round_logo)


@app.post("/api/frame")
async def upload_frame(request: Request):
    return await save_upload(request, watermark.save_frame)


@app.delete("/api/frame")
def delete_frame():
    watermark.FRAME_FILE.unlink(missing_ok=True)
    return settings_out()


@app.get("/api/frame.png")
def frame_png():
    if not watermark.FRAME_FILE.exists():
        raise HTTPException(404)
    return FileResponse(watermark.FRAME_FILE, media_type="image/png")


@app.delete("/api/watermark")
def delete_watermark():
    watermark.FILE.unlink(missing_ok=True)
    return settings_out()


@app.get("/api/watermark.png")
def watermark_png():
    if not watermark.FILE.exists():
        raise HTTPException(404)
    return FileResponse(watermark.FILE, media_type="image/png")


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
