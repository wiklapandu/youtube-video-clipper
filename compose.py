"""ffmpeg filter graph for one 1080x1920 segment.

Input 0 is the main video. The "stage" is where it plays: the whole frame (badge watermark mode, face-following
9:16 crop) or a 16:9 window in the middle of a 9:16 template image (frame mode). Extra YouTube videos are laid
over the stage for a time window (split / cutaway / pip, muted), then captions and the watermark go on top.
"""
import watermark

W, H = 1080, 1920
WINDOW_H = 608  # 1080 * 9 / 16, rounded to even
WINDOW_Y = (H - WINDOW_H) // 2


def even(n: float) -> int:
    return int(n) // 2 * 2


def segment_graph(*, follow: str, crop_for, fps, overlays=(), ass: str | None = None, wm=None,
                  logo_input: int | None = None, frame_input: int | None = None) -> str:
    """follow: filter making the main input 9:16 (badge mode). crop_for(aspect) -> fixed crop of the main input.
    overlays: [{"input": k, "mode": "split"|"cutaway"|"pip", "at": seconds, "dur": seconds}].
    Output pad: [v]."""
    frame = frame_input is not None
    sw, sh = (W, WINDOW_H) if frame else (W, H)
    splits = [o for o in overlays if o["mode"] == "split"]
    n = 1 + len(splits)
    parts = [f"[0:v]split={n}" + "".join(f"[m{i}]" for i in range(n)) if n > 1 else "[0:v]null[m0]"]
    if frame:
        parts.append(f"[m0]{crop_for(sw / sh)},scale={sw}:{sh},setsar=1[st0]")
    else:
        parts.append(f"[m0]{follow},scale={sw}:{sh}:force_original_aspect_ratio=decrease,"
                     f"pad={sw}:{sh}:(ow-iw)/2:(oh-ih)/2,setsar=1[st0]")
    cur, split_no = "st0", 1
    for i, o in enumerate(overlays):
        t, dur = o["at"], o["dur"]
        window = f"enable='between(t,{t:.3f},{t + dur:.3f})'"
        # shift the extra video so its first frame lands at t; before/after that the stage shows through
        src = f"[{o['input']}:v]trim=0:{dur:.3f},setpts=PTS-STARTPTS+{t:.3f}/TB"
        nxt = f"st{i + 1}"
        if o["mode"] == "cutaway":
            parts.append(f"{src},scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh},setsar=1[o{i}]")
            parts.append(f"[{cur}][o{i}]overlay=0:0:{window}:eof_action=pass[{nxt}]")
        elif o["mode"] == "pip":
            pw = even(sw * 0.42)
            parts.append(f"{src},scale={pw}:-2,pad=iw+12:ih+12:6:6:color=white,setsar=1[o{i}]")
            parts.append(f"[{cur}][o{i}]overlay={sw - pw - 12 - 32}:{24 if frame else 380}:{window}:eof_action=pass[{nxt}]")
        else:  # split: extra video on top/left, the main video in the rest
            if sh > sw:  # extra fills the top half; main stays whole 16:9, centered in the bottom half
                ow, oh, mw, mh, mx = sw, even(sh / 2), sw, WINDOW_H, 0
                my = oh + even((sh - oh - WINDOW_H) / 2)
            else:
                ow, oh = even(sw / 2), sh
                mw, mh, mx, my = ow, sh, ow, 0
            parts.append(f"{src},scale={ow}:{oh}:force_original_aspect_ratio=increase,crop={ow}:{oh},setsar=1[o{i}]")
            main = f"[m{split_no}]{crop_for(mw / mh)},scale={mw}:{mh},setsar=1"
            if sh > sw:  # blurred copy of the main fills the bars above/below it in the bottom half
                bh = sh - oh
                parts.append(f"{main},split[fg{i}][bl{i}]")
                parts.append(f"[bl{i}]scale={sw}:{bh}:force_original_aspect_ratio=increase,crop={sw}:{bh},"
                             f"boxblur=20:2[bb{i}]")
                parts.append(f"[bb{i}][fg{i}]overlay=0:{my - oh},pad={sw}:{sh}:0:{oh}[sp{i}]")
            else:
                parts.append(f"{main},pad={sw}:{sh}:{mx}:{my}[sp{i}]")
            parts.append(f"[sp{i}][o{i}]overlay=0:0:eof_action=pass[sc{i}]")
            parts.append(f"[{cur}][sc{i}]overlay=0:0:{window}[{nxt}]")
            split_no += 1
        cur = nxt
    if frame:  # template image is the looped main input of this overlay; shortest ends it with the video
        parts.append(f"[{frame_input}:v]fps={fps},scale={W}:{H},setsar=1[bg]")
        parts.append(f"[bg][{cur}]overlay=0:{WINDOW_Y}:shortest=1[full]")
        cur = "full"
    if ass:
        parts.append(f"[{cur}]ass={ass}[cap]")
        cur = "cap"
    if logo_input is not None:
        parts.append(watermark.overlay(cur, logo_input, wm, "v"))
    else:
        parts.append(f"[{cur}]null[v]")
    return ";".join(parts)
