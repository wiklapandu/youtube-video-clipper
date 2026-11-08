---
name: viral-clips
description: Find viral-worthy moments in a YouTube video and save them as clip candidates in the video-clipper website database. Use when the user sends a YouTube link and asks for viral moments, highlights, or AI clips (e.g. "/viral-clips <link>", "cariin momen viral", "potong jadi klip TikTok").
---

# Viral clips

Pick the best short-form moments from a YouTube video and save them as **candidates**. The user reviews, edits captions and renders them on the website (http://localhost:8000). Do not render or download video here.

Arguments: a YouTube URL, optionally a wish like jumlah klip, durasi, or topik. Defaults: 3–8 clips, each 15–60 seconds.

Run every command from the project root with the project venv.

## 1. Get the transcript

```bash
.venv/bin/python clipper.py transcript "<youtube-url>"
```

Prints JSON: `video_id`, `title`, `duration`, `source`, `transcript_path`. The website now shows the video as "AI sedang memilih momen viral…".
`source: whisper` means the video had no YouTube captions; that run can take many minutes for long videos, so run it in the background and tell the user.

Read the whole `transcript_path` file (read it in parts if it is long). Each line is `[m:ss] text`; times are seconds from the start of the video.

## 2. Pick the moments

Choose moments that work on TikTok/Reels/Shorts **without any other context**:

- Strong hook in the first 3 seconds: a bold claim, a question, a surprising fact, conflict, or an emotional line. Start the clip on that line, not on filler ("jadi", "oke", "um").
- One complete idea: setup → payoff (punchline, answer, reveal, lesson). End right after the payoff on a finished sentence, never mid-thought.
- High energy or emotion: humor, controversy, a strong opinion, a personal story, a practical tip, a quotable line.
- No overlap between clips; skip intros, sponsor reads, outros, and "like and subscribe".

Caption timestamps are approximate: start about 0.5 s before the first word of the hook and end about 1 s after the last word. Respect the duration limits.

For each clip write, in the video's language:
- `title`: a short catchy hook title (max ~60 chars) usable as the TikTok caption.
- `reason`: one sentence on why it can go viral.
- `score`: 0–100, your honest estimate of viral potential.

## 3. Save the candidates

Write the clips as a JSON array to a temp file in the scratchpad (seconds as numbers):

```json
[{"start": 754.5, "end": 801.0, "title": "...", "reason": "...", "score": 85}]
```

```bash
.venv/bin/python clipper.py add-clips <video_id> <path-to-json>
```

If it reports invalid clips, fix them and run it again. If the video has no usable moments, mark it instead:

```bash
.venv/bin/python clipper.py fail <video_id> "<short reason>"
```

## 4. Report

Tell the user, in their language, how many moments were saved with a short list (time range, title, score), and that they can open http://localhost:8000 to process (download + transcribe), edit captions and render each one. If the website is not running, say to start it with `npm run dev`.
