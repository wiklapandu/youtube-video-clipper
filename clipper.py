"""CLI for the viral-clips skill. Writes to the same database the website shows.

  clipper.py transcript <youtube-url>        -> fetch full transcript, prints JSON with transcript path
  clipper.py add-clips <video_id> <file|->   -> save AI-picked moments as clip candidates
  clipper.py fail <video_id> <message>       -> mark the AI analysis as failed
"""
import json
import sys

import db
import pipeline

MIN_LEN, MAX_LEN = 5, pipeline.MAX_CLIP_SECONDS


def validate_clips(clips, duration):
    """AI clip list -> cleaned rows. Raises ValueError with every problem found."""
    if not isinstance(clips, list) or not clips:
        raise ValueError("harus list klip yang tidak kosong")
    rows, errors = [], []
    for i, c in enumerate(clips):
        try:
            start, end = float(c["start"]), float(c["end"])
            title, reason = str(c["title"]).strip(), str(c.get("reason", "")).strip()
            hook = str(c.get("hook") or "").strip() or None
            score = int(c.get("score", 0))
        except (KeyError, TypeError, ValueError) as e:
            errors.append(f"#{i}: field salah/hilang ({e})")
            continue
        if not MIN_LEN <= end - start <= MAX_LEN:
            errors.append(f"#{i}: durasi {end - start:.1f}s harus {MIN_LEN}-{MAX_LEN}s")
        elif start < 0 or (duration and end > duration + 1):
            errors.append(f"#{i}: di luar durasi video ({duration}s)")
        elif not title:
            errors.append(f"#{i}: title kosong")
        elif hook and len(hook) > 200:
            errors.append(f"#{i}: hook maksimal 200 karakter")
        else:
            rows.append({"start": start, "end": min(end, duration or end), "title": title,
                         "reason": reason, "score": max(0, min(score, 100)), "hook": hook})
    if errors:
        raise ValueError("; ".join(errors))
    return rows


def cmd_transcript(url):
    if not pipeline.is_youtube(url):
        sys.exit("URL harus link YouTube")
    video_id = db.get_or_create_video(url)
    db.set_video(video_id, status="transcribing", message="Mengambil transkrip video…")
    d = pipeline.JOBS / "videos" / video_id
    d.mkdir(parents=True, exist_ok=True)
    try:
        info, source = pipeline.video_transcript(url, d)
    except Exception as e:
        db.set_video(video_id, status="error", message=f"Gagal ambil transkrip: {str(e)[:300]}")
        raise
    db.set_video(video_id, title=info.get("title"), duration=info.get("duration"),
                 status="analyzing", message="AI sedang memilih momen viral…")
    print(json.dumps({"video_id": video_id, "title": info.get("title"), "duration": info.get("duration"),
                      "source": source, "transcript_path": str(d / "transcript.txt")}, ensure_ascii=False))


def cmd_add_clips(video_id, path):
    video = db.get_video(video_id)
    if not video:
        sys.exit(f"video {video_id} tidak ada")
    raw = sys.stdin.read() if path == "-" else open(path, encoding="utf-8").read()
    try:
        rows = validate_clips(json.loads(raw), video["duration"])
    except (ValueError, json.JSONDecodeError) as e:
        sys.exit(f"Klip tidak valid: {e}")
    ids = [db.add_clip(video_id, source="ai", status="candidate", **r) for r in rows]
    db.set_video(video_id, status="ready", message=f"AI menemukan {len(ids)} momen")
    print(json.dumps({"video_id": video_id, "added": len(ids)}))


def main(argv):
    match argv:
        case ["transcript", url]:
            cmd_transcript(url)
        case ["add-clips", video_id, path]:
            cmd_add_clips(video_id, path)
        case ["fail", video_id, message]:
            db.set_video(video_id, status="error", message=message)
        case _:
            sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
