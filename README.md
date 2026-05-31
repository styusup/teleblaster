# TelegramScraper Rebuild

Rebuild implementasi berbasis README publik TelegramScraper.

## Status

Versi saat ini: `v0.1` (fitur inti sudah ada, beberapa alur advanced masih bertahap).

## Fitur v0.1

- Multi-session storage terenkripsi (Fernet + PBKDF2)
- Login akun via phone OTP (+ 2FA)
- Login via QR code (scan dari Telegram mobile)
- Import TData best-effort via session string converter eksternal
- Scrape members dari member list (visible)
- Scrape hidden participants dari message history + checkpoint
- Add members mode rush/calm
- Broadcast pesan dari markdown sederhana
- Session management (list/test/remove inactive)
- Cooldown persistence untuk FloodWait
- Atomic writes untuk JSON/CSV

## Quick Start (Windows, One-Click)

Untuk user yang tidak mau buka terminal, cukup **double-click `Run-GUI.bat`**.
Saat pertama dijalankan launcher akan otomatis:

1. Membuat virtual environment di `.venv/` (kalau belum ada).
2. Install dependencies dari `requirements.txt`.
3. Meminta `API_ID` + `API_HASH` (didapat dari https://my.telegram.org/apps)
   lalu menulis ke `.env`.
4. Menjalankan `gui_app.py`.

Dari run kedua dan seterusnya launcher akan menggunakan venv & .env yang sudah
ada dan langsung menampilkan GUI. Kalau ingin re-install dependencies, hapus
file `.venv/.tele_deps_ok`.

`Run-GUI.vbs` adalah varian silent (tanpa jendela CMD). Cocok dipakai jadi
shortcut di Desktop setelah setup awal selesai.

## Manual Setup (Linux / Mac / advanced users)

1. Install dependencies

```bash
pip install -r requirements.txt
```

2. Buat file `.env`

```env
API_ID=123456
API_HASH=your_api_hash
```

3. Jalankan

```bash
python main.py        # CLI
python gui_app.py     # Desktop GUI
```

## Desktop GUI

GUI menyediakan tab untuk Login, Scraper, Grup Scrapper, Adder, Broadcast, dan Sessions.

Di tab `Members Scraper`, gunakan tombol `Load My Joined Groups` setelah mengisi encryption password.
Daftar grup yang sudah diikuti akun login akan tampil, lalu klik `Use Selected Group` agar target scrape terisi otomatis.

Hasil scrape disimpan di **dua** lokasi:
- `members.csv` (gabungan semua scrape, dedup global) — dipakai tab `Broadcast`.
- `Hasil Scrape Member/<Nama Grup>.csv` (per-grup, dedup per file) — siap dipakai/di-share terpisah.
Nama file otomatis dibersihkan dari karakter ilegal Windows.

### Grup Scrapper

Tab `Grup Scrapper` memungkinkan cari grup/channel publik berdasarkan keyword niche (mis. `Affiliate Indonesia`)
dan langsung join dari aplikasi:

- Pilih akun di dropdown `Akun` (default `Auto (rotasi semua akun)`, atau pilih nomor akun spesifik
  agar Cari/Fetch/Join hanya pakai 1 akun tertentu — rotasi dimatikan). Klik `Refresh Akun` jika
  ada akun baru yang baru saja di-login.
- Isi keyword + encryption password.
- Pilih filter tipe (`Semua`, `Group/Supergroup saja`, atau `Channel saja`) dan limit hasil (1–100).
- Klik `Cari Grup` — pencarian memakai akun terpilih (atau rotasi otomatis kalau `Auto`) lewat API
  Telegram `contacts.Search`, hasilnya adalah grup/channel publik yang match keyword.
- Klik `Fetch Member Counts` (opsional) untuk mengisi kolom Members dengan nilai aktual via `get_chat`.
- Pilih beberapa baris lalu klik `Join Selected`, atau `Join All` untuk join seluruh hasil.
  Delay random antar join bisa disetel (default 5–15 detik) untuk hindari FloodWait.
- Centang `Skip grup/channel berlabel scam/fake` agar entri scam tidak ikut di-join.
- `Export CSV` menyimpan daftar hasil (judul, tipe, username, link, member count, status).
- Double-click baris untuk menyalin link `https://t.me/<username>` ke clipboard.

Catatan: Telegram membatasi hasil global search per query (~10–50 entri), gunakan keyword yang spesifik untuk
hasil terbaik. Grup tanpa username publik tidak bisa di-join otomatis dari sini — pakai invite link langsung
di tab `Members Adder`/`Broadcast` jika perlu.

Hasil scrape otomatis tersedia di tab `Broadcast` dalam bentuk daftar member yang bisa dipilih (multi-select).
Aktifkan opsi `Broadcast only selected members` untuk kirim hanya ke member yang dipilih.

Tab `Broadcast` juga mendukung compose langsung tanpa edit file:
- Input text pesan langsung di GUI
- Input link (satu link per baris)
- Lampiran file campuran (gambar, video, dokumen/teks)
- Kombinasi text + link + attachment dalam satu kali broadcast
- Setting delay acak min/max antar pengiriman (contoh 5 sampai 20 detik)

## Quick QR Test

Untuk uji cepat QR login end-to-end (tanpa menyimpan session), jalankan:

```bash
python quick_qr_test.py
```

Jika berhasil, script akan menampilkan info akun (`user id`, `phone`, `username`).

## Catatan

- Gunakan tool ini hanya pada akun dan grup yang Anda miliki izin untuk mengelola.
- Import TData langsung tanpa converter belum tersedia karena kompatibilitas format Telegram Desktop yang berubah antar versi.
