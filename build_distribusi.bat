@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

title Build Distribusi - Telegram Blaster By VibeTool.Club

echo ============================================================
echo   Build Distribusi - Telegram Blaster By VibeTool.Club
echo ============================================================
echo.
echo Skrip ini akan:
echo   1. Pastikan venv + dependency terinstall
echo   2. Install PyInstaller jika belum
echo   3. Build aplikasi jadi SATU file .exe (one-file)
echo   4. Copy "TelegramBlaster.exe" ke folder "Distribusi"
echo   5. Buat ZIP "Distribusi\TelegramBlaster.zip" siap kirim
echo.

REM ============================================================
REM 1. Pastikan ada Python
REM ============================================================
set "PY="
where py >nul 2>nul && set "PY=py -3"
if "%PY%"=="" where python >nul 2>nul && set "PY=python"
if "%PY%"=="" goto :no_python

REM ============================================================
REM 2. Cek / buat venv di .venv
REM ============================================================
if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Membuat virtual environment .venv ...
    %PY% -m venv .venv
    if errorlevel 1 goto :venv_failed
)

set "PYEXE=.venv\Scripts\python.exe"
set "PIP=.venv\Scripts\pip.exe"

REM ============================================================
REM 3. Install dependency app + PyInstaller
REM ============================================================
echo [INFO] Update pip ...
"%PYEXE%" -m pip install --upgrade pip >nul

echo [INFO] Install requirements.txt ...
"%PIP%" install -r requirements.txt
if errorlevel 1 goto :req_failed

echo [INFO] Install PyInstaller ...
"%PIP%" install --upgrade pyinstaller
if errorlevel 1 goto :pyi_install_failed

REM ============================================================
REM 4. .env opsional. Kalau ada, di-embed supaya client tidak perlu isi
REM    API_ID/API_HASH. Kalau tidak ada, aplikasi akan minta sekali saat
REM    pertama dijalankan lewat dialog GUI bawaan.
REM ============================================================
if exist ".env" (
    echo [INFO] .env terdeteksi, akan di-embed ke distribusi.
) else (
    echo [INFO] .env tidak ada - aplikasi akan minta API_ID/API_HASH saat first run.
)

REM ============================================================
REM 5. Bersihkan build lama
REM ============================================================
echo [INFO] Bersihkan build lama ...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist Distribusi\TelegramBlaster.exe del /q Distribusi\TelegramBlaster.exe
if exist Distribusi\TelegramBlaster.zip del /q Distribusi\TelegramBlaster.zip

REM ============================================================
REM 6. Run PyInstaller
REM ============================================================
echo [INFO] Build dengan PyInstaller (bisa 1-3 menit) ...
"%PYEXE%" -m PyInstaller teleblaster.spec --noconfirm
if errorlevel 1 goto :pyi_build_failed

if not exist "dist\TelegramBlaster.exe" goto :no_exe

REM ============================================================
REM 7. Copy .exe ke folder Distribusi + sertakan README untuk client
REM ============================================================
if not exist Distribusi mkdir Distribusi
echo [INFO] Copy TelegramBlaster.exe ke Distribusi ...
copy /y "dist\TelegramBlaster.exe" "Distribusi\TelegramBlaster.exe" >nul

REM ============================================================
REM 8. ZIP .exe (+ README bila ada) supaya gampang dikirim
REM ============================================================
echo [INFO] Membuat ZIP Distribusi\TelegramBlaster.zip ...
if exist "Distribusi\README-CLIENT.txt" (
    powershell -NoProfile -Command "Compress-Archive -Path 'Distribusi\TelegramBlaster.exe','Distribusi\README-CLIENT.txt' -DestinationPath 'Distribusi\TelegramBlaster.zip' -Force"
) else (
    powershell -NoProfile -Command "Compress-Archive -Path 'Distribusi\TelegramBlaster.exe' -DestinationPath 'Distribusi\TelegramBlaster.zip' -Force"
)
if errorlevel 1 echo [WARNING] Gagal buat ZIP, .exe tetap tersedia.

echo.
echo ============================================================
echo   BUILD SELESAI
echo ============================================================
echo.
echo File EXE tunggal  :  Distribusi\TelegramBlaster.exe
echo File ZIP siap kirim:  Distribusi\TelegramBlaster.zip
echo.
echo Cara test sebelum kirim ke client:
echo   1. Buka folder Distribusi
echo   2. Double-click TelegramBlaster.exe
echo   3. Pastikan GUI muncul dan login bisa berjalan
echo.
pause
exit /b 0


REM ============================================================
REM Error handlers (terpisah dari blok if untuk hindari bug parser)
REM ============================================================
:no_python
echo.
echo [ERROR] Python tidak ditemukan.
echo Install Python dari https://www.python.org/downloads/ lalu coba lagi.
echo.
pause
exit /b 1

:venv_failed
echo.
echo [ERROR] Gagal membuat virtual environment di .venv
echo.
pause
exit /b 1

:req_failed
echo.
echo [ERROR] Gagal install requirements.txt
echo Pastikan koneksi internet aktif.
echo.
pause
exit /b 1

:pyi_install_failed
echo.
echo [ERROR] Gagal install PyInstaller
echo Pastikan koneksi internet aktif.
echo.
pause
exit /b 1

:pyi_build_failed
echo.
echo [ERROR] PyInstaller gagal melakukan build.
echo Lihat log di atas untuk detail error.
echo.
pause
exit /b 1

:no_exe
echo.
echo [ERROR] Output build tidak ditemukan di
echo   dist\TelegramBlaster.exe
echo PyInstaller mungkin gagal silent. Coba ulang skrip.
echo.
pause
exit /b 1
