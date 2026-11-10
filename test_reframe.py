from reframe import crop_width, plan_track, sendcmd_script


def test_crop_width():
    assert crop_width(1920, 1080) == 606
    assert crop_width(1080, 1920) == 1080  # vertical source keeps full width


def test_no_face_stays_centered():
    assert plan_track([None, None], 1920, 606) == [657, 657]


def test_fills_missing_and_clamps():
    # leading None uses first known face; face at far right clamps to the edge
    assert plan_track([None, 1900, None], 1920, 606) == [1314, 1314, 1314]


def test_moves_smoothly_and_ignores_jitter():
    xs = plan_track([303, 310, 1500, 1500, 1500], 1920, 606)
    assert xs[:2] == [0, 0]  # 7px jitter ignored
    assert 0 < xs[2] < xs[3] < xs[4] < 1197  # eases toward the new face, no jump


def test_sendcmd_only_emits_changes():
    script = sendcmd_script([100, 100, 200], fps=10, n_frames=5)
    assert script.splitlines()[0] == "0.000 crop@rf x 100;"
    assert all(line.endswith(";") for line in script.splitlines())
    assert len(script.splitlines()) == 3  # frames 0 (100), 3 (150), 4 (200)


def test_static_crop():
    from reframe import static_crop
    wide = {"iw": 1920, "ih": 1080, "centers": [None, 1700, 1800]}
    assert static_crop(wide, 9 / 8) == "crop=1214:1080:706:0"  # follows the face, clamped to the right edge
    assert static_crop({**wide, "centers": []}, 9 / 16) == "crop=606:1080:656:0"  # no face: middle
    assert static_crop({"iw": 1080, "ih": 1920, "centers": []}, 16 / 9) == "crop=1080:606:0:656"  # tall source
