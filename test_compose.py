from compose import segment_graph

crop_for = lambda a: f"crop_for({a:.3f})"  # noqa: E731


def test_badge_with_overlays():
    g = segment_graph(follow="FOLLOW", crop_for=crop_for, fps="30/1", ass="captions.ass",
                      wm={"position": "tl"}, logo_input=3,
                      overlays=[{"input": 1, "mode": "split", "at": 2, "dur": 4},
                                {"input": 2, "mode": "pip", "at": 7.5, "dur": 3}])
    assert g.startswith("[0:v]split=2[m0][m1];[m0]FOLLOW,scale=1080:1920")
    assert "[1:v]trim=0:4.000,setpts=PTS-STARTPTS+2.000/TB,scale=1080:960" in g
    assert "[m1]crop_for(1.776),scale=1080:608,setsar=1,split[fg0][bl0]" in g
    assert "crop=1080:960,boxblur=20:2[bb0];[bb0][fg0]overlay=0:176,pad=1080:1920:0:960[sp0]" in g
    assert "[st0][sc0]overlay=0:0:enable='between(t,2.000,6.000)'[st1]" in g
    assert "overlay=584:380:enable='between(t,7.500,10.500)':eof_action=pass[st2]" in g
    assert "[st2]ass=captions.ass[cap]" in g and g.endswith("overlay=44:150[v]")


def test_frame_mode_window_and_side_by_side_split():
    g = segment_graph(follow="FOLLOW", crop_for=crop_for, fps="25/1", frame_input=2,
                      overlays=[{"input": 1, "mode": "split", "at": 0, "dur": 3}])
    assert "[m0]crop_for(1.776),scale=1080:608" in g and "FOLLOW" not in g
    assert "[m1]crop_for(0.888),scale=540:608,setsar=1,pad=1080:608:540:0" in g
    assert "[2:v]fps=25/1,scale=1080:1920,setsar=1[bg];[bg][st1]overlay=0:656:shortest=1[full]" in g
    assert g.endswith("[full]null[v]")
