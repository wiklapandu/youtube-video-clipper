# Video Clipper

Bikin klip 9:16 dari video YouTube, dengan caption karaoke dan crop yang otomatis mengikuti wajah.

Klip bisa dibuat dengan dua cara, dan semuanya muncul di website beserta progresnya:
- **Manual**: di website, isi link + menit mulai/selesai.
- **AI**: di chat Claude Code (di folder project ini), ketik `/viral-clips <link YouTube>`. AI membaca transkrip, memilih momen viral, lalu menyimpannya sebagai kandidat (judul, alasan, skor).

Lalu per klip di website: **Proses** (download + transkrip) → **Edit caption** → **Render** → **Download**. **Proses ulang** mengulang download + transkrip dari awal. Di editor juga bisa:
- **Durasi klip**: ubah Mulai/Selesai (atau ±1 detik), lalu klip didownload & ditranskrip ulang.
- **Video tambahan** dari YouTube (mis. highlight bola, maks 60 detik): split atas-bawah, cutaway, picture-in-picture (tanpa suara, muncul di detik tertentu), atau disambung di awal/akhir (dengan suaranya).

**Watermark**: di panel Watermark website, upload logo (dipotong bulat seperti foto profil), isi teks handle, pilih posisi. Atau pilih **Bingkai 9:16**: upload gambar tegak 1080x1920, video landscape tampil di tengahnya (y=656, tinggi 608px) dan caption di area bawah. Disimpan di `data/watermark.png` dan database; berlaku untuk render berikutnya, termasuk intro hook.

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
- `captions.py` caption karaoke, `reframe.py` crop ikut wajah, `watermark.py` logo/handle/bingkai, `compose.py` susunan video (overlay, bingkai)

Opsi env: `WHISPER_MODEL` (default `small`; `medium` lebih akurat tapi lambat), `FFMPEG` (path ffmpeg), `HOOK_MAC_VOICE` (suara cadangan Mac kalau Edge TTS gagal, default `Damayanti`).

Suara hook memakai Edge TTS (Ardi/Gadis, gratis, perlu internet). Kalau gagal, otomatis pakai suara Mac dan kartu klip menampilkan pesannya.
