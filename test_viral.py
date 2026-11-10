import pytest

import db
from clipper import validate_clips
from pipeline import format_transcript, json3_chunks, pick_subtitle_url


def test_json3_chunks_split_on_length_and_sentence():
    events = [
        {"tStartMs": 0, "dDurationMs": 9000},  # window event, no segs
        {"tStartMs": 500, "segs": [{"utf8": "halo"}, {"utf8": " semua."}]},
        {"tStartMs": 1500, "aAppend": 1, "segs": [{"utf8": "\n"}]},
        {"tStartMs": 2000, "segs": [{"utf8": "hari ini"}]},
        {"tStartMs": 11000, "segs": [{"utf8": "kita masak"}]},
    ]
    assert json3_chunks(events) == [(0.5, "halo semua."), (2.0, "hari ini"), (11.0, "kita masak")]
    assert format_transcript([(65.4, "x")]) == "[1:05] x"


def test_pick_subtitle_prefers_manual_then_orig_auto():
    j = lambda u: [{"ext": "vtt", "url": "v"}, {"ext": "json3", "url": u}]
    info = {"language": "id", "subtitles": {"id": j("manual-id")}, "automatic_captions": {"id-orig": j("auto")}}
    assert pick_subtitle_url(info) == "manual-id"
    assert pick_subtitle_url({**info, "subtitles": {}}) == "auto"
    assert pick_subtitle_url({"subtitles": {"en": [{"ext": "vtt", "url": "v"}]}}) is None


def test_validate_clips():
    rows = validate_clips([{"start": 10, "end": 40, "title": " Hook ", "score": 150}], duration=100)
    assert rows == [{"start": 10.0, "end": 40.0, "title": "Hook", "reason": "", "score": 100, "hook": None}]
    rows = validate_clips([{"start": 0, "end": 20, "title": "t", "hook": " Momen ketika dia bilang 'cukup' "}], 100)
    assert rows[0]["hook"] == "Momen ketika dia bilang 'cukup'"
    with pytest.raises(ValueError, match="#0.*#1"):
        validate_clips([{"start": 10, "end": 12, "title": "pendek"}, {"start": 90, "end": 130, "title": "lewat"}], 100)
    with pytest.raises(ValueError):
        validate_clips([], 100)


def test_db_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    vid = db.get_or_create_video("https://youtu.be/x")
    assert db.get_or_create_video("https://youtu.be/x") == vid
    cid = db.add_clip(vid, 1, 20, "ai", "candidate", title="t", score=80)
    db.set_clip(cid, status="downloading")
    db.fail_interrupted_clips()
    (video,) = db.list_videos()
    assert video["clips"][0]["status"] == "error"
    assert db.get_clip(cid)["url"] == "https://youtu.be/x"
    with pytest.raises(ValueError):
        db.set_clip(cid, source="manual")


def test_speak_falls_back_to_mac_voice(tmp_path, monkeypatch):
    import edge_tts
    import pipeline

    def boom(*a, **k):
        raise ConnectionError("offline")

    calls = []
    monkeypatch.setattr(edge_tts.Communicate, "save_sync", boom)
    monkeypatch.setattr(pipeline, "_run", lambda cmd, cwd: calls.append(cmd))
    audio, note = pipeline.speak(tmp_path, "halo", "gadis")
    assert audio == "hook.aiff" and "ConnectionError" in note
    assert calls[0][:3] == ["say", "-v", pipeline.MAC_VOICE]
