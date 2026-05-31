#!/usr/bin/env bash
# Linux smoke-test build (untuk verifikasi spec PyInstaller).
# Untuk build distribusi Windows yang dikirim ke client, jalankan
# build_distribusi.bat di mesin Windows.

set -euo pipefail
cd "$(dirname "$0")"

echo "[INFO] Build Distribusi (Linux smoke test) ..."

if [[ ! -d ".venv" ]]; then
    python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
pip install --quiet --upgrade pyinstaller

rm -rf build dist Distribusi/TelegramBlaster Distribusi/TelegramBlaster.tar.gz

pyinstaller teleblaster.spec --noconfirm

# One-file build menghasilkan satu file executable (tanpa ekstensi di Linux).
if [[ ! -f "dist/TelegramBlaster" ]]; then
    echo "[ERROR] Output tidak ditemukan." >&2
    exit 1
fi

mkdir -p Distribusi
cp -f dist/TelegramBlaster Distribusi/TelegramBlaster

(cd Distribusi && tar -czf TelegramBlaster.tar.gz TelegramBlaster)

echo "[OK] File tunggal : Distribusi/TelegramBlaster"
echo "[OK] Tarball      : Distribusi/TelegramBlaster.tar.gz"
