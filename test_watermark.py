import cv2
import numpy as np
import pytest

import db
import watermark


@pytest.fixture
def wm_file(tmp_path, monkeypatch):
    monkeypatch.setattr(watermark, "FILE", tmp_path / "data" / "watermark.png")
    monkeypatch.setattr(watermark, "FRAME_FILE", tmp_path / "data" / "frame.png")
    return watermark.FILE


def encode(img, ext=".png"):
    return cv2.imencode(ext, img)[1].tobytes()


def test_round_logo_from_wide_jpg(wm_file):
    img = np.full((100, 300, 3), (0, 0, 255), np.uint8)  # wide red image
    watermark.save_round_logo(encode(img, ".jpg"))
    out = cv2.imread(str(wm_file), cv2.IMREAD_UNCHANGED)
    s = watermark.SIZE * 2
    assert out.shape == (s, s, 4)
    assert out[0, 0, 3] == 0 and out[s // 2, s // 2, 3] == 255  # transparent corner, solid center
    assert out[s // 2, s // 2, 2] > 200  # still red


def test_transparent_png_gets_white_background(wm_file):
    img = np.zeros((50, 50, 4), np.uint8)  # fully transparent
    watermark.save_round_logo(encode(img))
    out = cv2.imread(str(wm_file), cv2.IMREAD_UNCHANGED)
    assert tuple(out[watermark.SIZE, watermark.SIZE]) == (255, 255, 255, 255)


def test_rejects_non_images_and_big_files(wm_file):
    with pytest.raises(ValueError):
        watermark.save_round_logo(b"not an image")
    with pytest.raises(ValueError, match="5 MB"):
        watermark.save_round_logo(b"0" * (watermark.MAX_UPLOAD + 1))
    assert not wm_file.exists()


def test_active_and_handle_event(wm_file):
    base = {"watermark_enabled": "1", "watermark_handle": " @kanal ", "watermark_position": "tr"}
    assert watermark.active({**base, "watermark_enabled": "0"}) is None
    assert watermark.active({**base, "watermark_handle": ""}) is None  # no logo, no text
    wm = watermark.active(base)
    assert wm == {"mode": "badge", "logo": None, "handle": "@kanal", "position": "tr"}
    # no logo: text sits where the logo would be, right-aligned to its right edge
    assert watermark.handle_event(wm).endswith("Handle,,0,0,0,,{\\an6\\pos(1036,214)}@kanal\n")
    wm_file.parent.mkdir()
    wm_file.write_bytes(b"x")
    assert watermark.active({**base, "watermark_position": "zz"})["position"] == "tl"


def test_settings_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    assert db.get_settings()["watermark_position"] == "tl"
    db.set_settings(watermark_handle="@a", watermark_position="bc")
    assert db.get_settings() == {"watermark_enabled": "1", "watermark_handle": "@a", "watermark_position": "bc", "watermark_mode": "badge"}
    with pytest.raises(ValueError):
        db.set_settings(evil="1")


def test_frame_template(wm_file):
    img = np.full((1000, 1000, 3), (10, 200, 30), np.uint8)  # square: covers 1080x1920, sides cropped
    watermark.save_frame(encode(img))
    out = cv2.imread(str(watermark.FRAME_FILE))
    assert out.shape == (1920, 1080, 3) and tuple(out[960, 540]) == (10, 200, 30)
    on = {"watermark_enabled": "1", "watermark_handle": "@a", "watermark_position": "tl", "watermark_mode": "frame"}
    assert watermark.active(on)["frame"] == watermark.FRAME_FILE and watermark.handle_event(watermark.active(on)) == ""
    watermark.FRAME_FILE.unlink()
    assert watermark.active(on)["mode"] == "badge"  # no template uploaded: falls back to logo/handle
