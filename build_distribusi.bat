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
echo   3. Build aplikasi jadi folder portable
echo   4. Copy hasilnya ke folder "Distribusi\TelegramBlaster"
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
REM 4. Cek .env wajib ada (akan di-embed ke distribusi)
REM ============================================================
if not exist ".env" goto :env_missing
echo [INFO] .env terdeteksi, akan di-embed ke distribusi.

REM ============================================================
REM 5. Bersihkan build lama
REM ============================================================
echo [INFO] Bersihkan build lama ...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist Distribusi\TelegramBlaster rmdir /s /q Distribusi\TelegramBlaster
if exist Distribusi\TelegramBlaster.zip del /q Distribusi\TelegramBlaster.zip

REM ============================================================
REM 6. Run PyInstaller
REM ============================================================
echo [INFO] Build dengan PyInstaller (bisa 1-3 menit) ...
"%PYEXE%" -m PyInstaller teleblaster.spec --noconfirm
if errorlevel 1 goto :pyi_build_failed

if not exist "dist\TelegramBlaster\TelegramBlaster.exe" goto :no_exe

REM ============================================================
REM 7. Copy ke folder Distribusi + sertakan README untuk client
REM ============================================================
if not exist Distribusi mkdir Distribusi
echo [INFO] Copy hasil build ke Distribusi\TelegramBlaster ...
xcopy /e /i /q /y "dist\TelegramBlaster" "Distribusi\TelegramBlaster" >nul
if exist "Distribusi\README-CLIENT.txt" copy /y "Distribusi\README-CLIENT.txt" "Distribusi\TelegramBlaster\README.txt" >nul

REM ============================================================
REM 8. ZIP folder hasil supaya gampang dikirim
REM ============================================================
echo [INFO] Membuat ZIP Distribusi\TelegramBlaster.zip ...
powershell -NoProfile -Command "Compress-Archive -Path 'Distribusi\TelegramBlaster\*' -DestinationPath 'Distribusi\TelegramBlaster.zip' -Force"
if errorlevel 1 echo [WARNING] Gagal buat ZIP, folder tetap tersedia.

echo.
echo ============================================================
echo   BUILD SELESAI
echo ============================================================
echo.
echo Folder portable :  Distribusi\TelegramBlaster\
echo File ZIP siap kirim:  Distribusi\TelegramBlaster.zip
echo.
echo Cara test sebelum kirim ke client:
echo   1. Buka Distribusi\TelegramBlaster
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

:env_missing
echo.
echo [ERROR] File .env tidak ditemukan di folder ini.
echo.
echo Build dihentikan karena distribusi WAJIB punya .env yang berisi
echo API_ID dan API_HASH. Tanpa itu, client harus mengisi credential
echo sendiri, dan itu bertentangan dengan tujuan distribusi siap pakai.
echo.
echo CARA FIX:
echo   1. Buka https://my.telegram.org/apps di browser, login dengan
echo      nomor Telegram Anda.
echo   2. Buka bagian App configuration. Catat App api_id berupa angka
echo      dan App api_hash berupa string panjang.
echo   3. Buat file .env di folder ini berisi:
echo          API_ID=12345678
echo          API_HASH=0123456789abcdef0123456789abcdef
echo   4. Jalankan ulang build_distribusi.bat
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
echo   dist\TelegramBlaster\TelegramBlaster.exe
echo PyInstaller mungkin gagal silent. Coba ulang skrip.
echo.
pause
exit /b 1
