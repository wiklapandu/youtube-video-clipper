"""SQLite store shared by the website and the clipper CLI (used by the viral-clips skill)."""
import sqlite3
import time
import uuid
from pathlib import Path

DB_PATH = Path(__file__).parent / "clipper.db"

SCHEMA = """
create table if not exists videos(
  id text primary key, url text unique not null, title text, duration real,
  status text not null default 'new', message text, created_at real not null);
create table if not exists clips(
  id text primary key, video_id text not null references videos(id) on delete cascade,
  start real not null, end real not null, title text, reason text, score integer,
  source text not null check (source in ('manual', 'ai')),
  status text not null, message text, created_at real not null, hook text);
create table if not exists settings(key text primary key, value text);
create table if not exists inserts(
  id text primary key, clip_id text not null references clips(id) on delete cascade,
  url text not null, src_start real not null, src_end real not null,
  mode text not null check (mode in ('split', 'cutaway', 'pip', 'before', 'after')), at real,
  status text not null, message text, created_at real not null);
"""
SETTING_DEFAULTS = {"watermark_enabled": "1", "watermark_handle": "", "watermark_position": "tl", "watermark_mode": "badge"}
VIDEO_FIELDS = {"title", "duration", "status", "message"}
CLIP_FIELDS = {"status", "message", "hook", "start", "end"}
INSERT_FIELDS = {"status", "message"}
# statuses that mean a worker thread is on it; a server restart kills those threads
BUSY = ("queued", "downloading", "transcribing", "rendering")


def connect():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("pragma journal_mode=wal")  # website and CLI write at the same time
    c.execute("pragma foreign_keys=on")
    c.executescript(SCHEMA)
    if "hook" not in {r["name"] for r in c.execute("pragma table_info(clips)")}:  # DBs made before hooks
        c.execute("alter table clips add column hook text")
    return c


def _update(table, allowed, id_, fields):
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"unknown fields: {bad}")
    sets = ", ".join(f"{k} = ?" for k in fields)
    with connect() as c:
        c.execute(f"update {table} set {sets} where id = ?", [*fields.values(), id_])


def get_or_create_video(url: str) -> str:
    with connect() as c:
        row = c.execute("select id from videos where url = ?", (url,)).fetchone()
        if row:
            return row["id"]
        vid = uuid.uuid4().hex
        c.execute("insert into videos(id, url, created_at) values (?, ?, ?)", (vid, url, time.time()))
        return vid


def set_video(video_id: str, **fields):
    _update("videos", VIDEO_FIELDS, video_id, fields)


def add_clip(video_id, start, end, source, status, title=None, reason=None, score=None, hook=None) -> str:
    cid = uuid.uuid4().hex
    with connect() as c:
        c.execute(
            "insert into clips(id, video_id, start, end, title, reason, score, source, status, created_at, hook)"
            " values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, video_id, start, end, title, reason, score, source, status, time.time(), hook),
        )
    return cid


def set_clip(clip_id: str, **fields):
    _update("clips", CLIP_FIELDS, clip_id, fields)


def get_video(video_id: str):
    with connect() as c:
        return c.execute("select * from videos where id = ?", (video_id,)).fetchone()


def get_clip(clip_id: str):
    """Clip row joined with its video url."""
    with connect() as c:
        return c.execute(
            "select clips.*, videos.url from clips join videos on videos.id = clips.video_id where clips.id = ?",
            (clip_id,),
        ).fetchone()


def delete_clip(clip_id: str):
    with connect() as c:
        c.execute("delete from clips where id = ?", (clip_id,))


def list_videos():
    with connect() as c:
        videos = [dict(v) for v in c.execute("select * from videos order by created_at desc")]
        clips = [dict(r) for r in c.execute("select * from clips order by start")]
        inserts = [dict(r) for r in c.execute("select * from inserts order by created_at")]
    for cl in clips:
        cl["inserts"] = [i for i in inserts if i["clip_id"] == cl["id"]]
    for v in videos:
        v["clips"] = [cl for cl in clips if cl["video_id"] == v["id"]]
    return videos


def fail_interrupted_clips():
    with connect() as c:
        for table in ("clips", "inserts"):
            c.execute(
                f"update {table} set status = 'error', message = 'Terputus karena server restart, coba lagi'"
                f" where status in ({','.join('?' * len(BUSY))})",
                BUSY,
            )


def add_insert(clip_id, url, src_start, src_end, mode, at) -> str:
    iid = uuid.uuid4().hex
    with connect() as c:
        c.execute("insert into inserts(id, clip_id, url, src_start, src_end, mode, at, status, created_at)"
                  " values (?, ?, ?, ?, ?, ?, ?, 'queued', ?)", (iid, clip_id, url, src_start, src_end, mode, at, time.time()))
    return iid


def set_insert(insert_id: str, **fields):
    _update("inserts", INSERT_FIELDS, insert_id, fields)


def get_insert(insert_id: str):
    with connect() as c:
        return c.execute("select * from inserts where id = ?", (insert_id,)).fetchone()


def clip_inserts(clip_id: str):
    with connect() as c:
        return [dict(r) for r in c.execute("select * from inserts where clip_id = ? order by created_at", (clip_id,))]


def delete_insert(insert_id: str):
    with connect() as c:
        c.execute("delete from inserts where id = ?", (insert_id,))


def get_settings() -> dict:
    with connect() as c:
        stored = dict(c.execute("select key, value from settings").fetchall())
    return {**SETTING_DEFAULTS, **{k: v for k, v in stored.items() if k in SETTING_DEFAULTS}}


def set_settings(**values):
    bad = set(values) - set(SETTING_DEFAULTS)
    if bad:
        raise ValueError(f"unknown settings: {bad}")
    with connect() as c:
        c.executemany("insert into settings(key, value) values (?, ?) on conflict(key) do update set value = excluded.value",
                      values.items())
