# Video Clipper

Bikin klip 9:16 dari video YouTube, dengan caption karaoke dan crop yang otomatis mengikuti wajah.

Klip bisa dibuat dengan dua cara, dan semuanya muncul di website beserta progresnya:
- **Manual**: di website, isi link + menit mulai/selesai.
- **AI**: di chat Claude Code (di folder project ini), ketik `/viral-clips <link YouTube>`. AI membaca transkrip, memilih momen viral, lalu menyimpannya sebagai kandidat (judul, alasan, skor).

Lalu per klip di website: **Proses** (download + transkrip) → **Edit caption** → **Render** → **Download**.

## Perintah
```bash
npm run setup   # sekali: pasang ffmpeg-full, buat .venv (Python 3.14), install paket Python
npm run dev     # jalankan di http://localhost:8000 (auto-reload)
npm start       # jalankan tanpa auto-reload
npm test        # tes
npm run clean   # hapus semua file hasil di jobs/
```

Butuh Homebrew dan Python 3.14 (`brew install python@3.14`). Python 3.15 belum didukung faster-whisper; ffmpeg biasa tidak punya filter subtitle, makanya pakai `ffmpeg-full`.

## Struktur
- `app.py` website + API (proses jalan di latar belakang, satu per satu)
- `clipper.py` CLI yang dipakai skill (`transcript`, `add-clips`, `fail`)
- `.claude/skills/viral-clips/SKILL.md` instruksi skill AI
- `db.py` database SQLite (`clipper.db`), `pipeline.py` download/transkrip/render
- `captions.py` caption karaoke, `reframe.py` crop ikut wajah

Opsi env: `WHISPER_MODEL` (default `small`; `medium` lebih akurat tapi lambat), `FFMPEG` (path ffmpeg).
