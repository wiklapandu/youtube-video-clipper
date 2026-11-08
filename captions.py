"""Pure caption helpers: time parsing, word grouping, retiming after edits, ASS karaoke output."""
import re

PUNCT_END = (".", ",", "?", "!", ";", ":")


def parse_time(value: str) -> float:
    """'90', '1:30', '1:02:03' (optionally with decimals) -> seconds."""
    parts = value.strip().split(":")
    if not 1 <= len(parts) <= 3 or not all(re.fullmatch(r"\d+(\.\d+)?", p) for p in parts):
        raise ValueError(f"format waktu tidak valid: {value!r}")
    seconds = 0.0
    for p in parts:
        seconds = seconds * 60 + float(p)
    return seconds


def group_words(words, max_words=4, max_dur=1.5, max_gap=0.6):
    """words: [{word, start, end}] -> lines: [{start, end, words}]."""
    lines, cur = [], []
    for w in words:
        if cur:
            prev = cur[-1]
            if (
                len(cur) >= max_words
                or w["end"] - cur[0]["start"] > max_dur
                or w["start"] - prev["end"] > max_gap
                or prev["word"].endswith(PUNCT_END)
            ):
                lines.append(cur)
                cur = []
        cur.append(w)
    if cur:
        lines.append(cur)
    return [{"start": l[0]["start"], "end": l[-1]["end"], "words": l} for l in lines]


def retime(line, text: str):
    """Apply edited text to a line. Same word count keeps timings, otherwise spread evenly.
    Returns None when the text is empty (line removed)."""
    new = text.split()
    if not new:
        return None
    old = line["words"]
    if len(new) == len(old):
        words = [{**w, "word": t} for w, t in zip(old, new)]
    else:
        step = (line["end"] - line["start"]) / len(new)
        words = [
            {"word": t, "start": line["start"] + i * step, "end": line["start"] + (i + 1) * step}
            for i, t in enumerate(new)
        ]
    return {"start": line["start"], "end": line["end"], "words": words}


def _ass_time(t: float) -> str:
    cs = round(t * 100)
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clean(text: str) -> str:
    return text.replace("\\", "").replace("{", "").replace("}", "")


ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,84,&H0000FFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,420,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def to_ass(lines) -> str:
    """Karaoke: each word starts white (Secondary) and turns yellow (Primary) when spoken."""
    out = [ASS_HEADER]
    for line in lines:
        words = line["words"]
        parts = []
        for i, w in enumerate(words):
            nxt = words[i + 1]["start"] if i + 1 < len(words) else line["end"]
            # rounding on absolute times keeps the \k sum equal to the event length
            dur = max(0, round(nxt * 100) - round(w["start"] * 100))
            parts.append(f"{{\\k{dur}}}{_clean(w['word'])}")
        start = min(line["start"], words[0]["start"])
        out.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(line['end'])},Default,,0,0,0,,{' '.join(parts)}\n")
    return "".join(out)
