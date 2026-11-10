import re

import pytest

from captions import group_words, parse_time, retime, to_ass


def w(word, start, end):
    return {"word": word, "start": start, "end": end}


def test_parse_time():
    assert parse_time("90") == 90
    assert parse_time("1:30") == 90
    assert parse_time("1:02:03") == 3723
    assert parse_time(" 0:05.5 ") == 5.5
    for bad in ["", "a:b", "1:2:3:4", "-5"]:
        with pytest.raises(ValueError):
            parse_time(bad)


def test_group_words_splits_on_count_gap_and_punct():
    words = [w("a", 0, 0.2), w("b", 0.2, 0.4), w("c", 0.4, 0.6), w("d", 0.6, 0.8), w("e", 0.8, 1.0),
             w("f.", 3.0, 3.2), w("g", 3.2, 3.4)]
    lines = group_words(words)
    assert [[x["word"] for x in l["words"]] for l in lines] == [["a", "b", "c", "d"], ["e"], ["f."], ["g"]]
    assert lines[0]["start"] == 0 and lines[0]["end"] == 0.8


def test_retime():
    line = {"start": 1.0, "end": 2.0, "words": [w("helo", 1.0, 1.4), w("dunia", 1.5, 2.0)]}
    same = retime(line, "halo dunia")
    assert [x["word"] for x in same["words"]] == ["halo", "dunia"] and same["words"][1]["start"] == 1.5
    more = retime(line, "halo semua dunia")
    assert [round(x["start"], 3) for x in more["words"]] == [1.0, 1.333, 1.667]
    assert retime(line, "   ") is None


def test_to_ass_karaoke_durations_match_event():
    line = {"start": 1.0, "end": 2.0, "words": [w("halo", 1.0, 1.4), w("{dunia}", 1.5, 2.0)]}
    dialogue = to_ass([line]).splitlines()[-1]
    assert dialogue.startswith("Dialogue: 0,0:00:01.00,0:00:02.00,")
    assert sum(map(int, re.findall(r"\\k(\d+)", dialogue))) == 100
    assert dialogue.endswith("{\\k50}halo {\\k50}dunia")


def test_hook_ass_highlights_quotes():
    from captions import hook_ass
    line = hook_ass("Rahasia 'pintu' hak siar: {jangan}", 3.2).splitlines()[-1]
    assert line.startswith("Dialogue: 0,0:00:00.00,0:00:03.20,Hook,")
    assert "{\\c&H0012C3FF&}'pintu'{\\r} hak siar: jangan" in line
