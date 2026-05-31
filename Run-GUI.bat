@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

title Telegram Blaster By VibeTool.Club

echo ========================================================
echo   Telegram Blaster By VibeTool.Club - One-Click Launcher
echo ========================================================
echo.

REM --- 1) Pilih Python: prefer ./.venv, lalu ../.venv, lalu buat baru
set "VENVDIR=%~dp0.venv"
set "PYTHON="

if exist "%VENVDIR%\Scripts\python.exe" (
    set "PYTHON=%VENVDIR%\Scripts\python.exe"
    echo [OK] Pakai venv lokal: %VENVDIR%
    goto have_python
)

if exist "%~dp0..\.venv\Scripts\python.exe" (
    set "VENVDIR=%~dp0..\.venv"
    set "PYTHON=%~dp0..\.venv\Scripts\python.exe"
    echo [OK] Pakai venv parent: !VENVDIR!
    goto have_python
)

echo [..] Belum ada venv. Membuat venv baru di "%VENVDIR%" ...
set "PY_BOOTSTRAP="
where py >nul 2>nul
if !errorlevel! == 0 set "PY_BOOTSTRAP=py -3"
if not defined PY_BOOTSTRAP (
    where python >nul 2>nul
    if !errorlevel! == 0 set "PY_BOOTSTRAP=python"
)
if not defined PY_BOOTSTRAP (
    echo [ERROR] Python tidak ditemukan di PATH.
    echo         Install Python 3.10+ dari https://www.python.org/downloads/
    echo         Centang "Add Python to PATH" saat install.
    pause
    exit /b 1
)
%PY_BOOTSTRAP% -m venv "%VENVDIR%"
if not exist "%VENVDIR%\Scripts\python.exe" (
    echo [ERROR] Gagal membuat venv di "%VENVDIR%".
    pause
    exit /b 1
)
set "PYTHON=%VENVDIR%\Scripts\python.exe"
echo [OK] venv baru dibuat: %VENVDIR%

:have_python

REM --- 2) Install / update dependencies bila marker belum ada.
REM     Hapus file .tele_deps_ok di dalam venv kalau mau force re-install.
set "MARKER=%VENVDIR%\.tele_deps_ok"
if not exist "%MARKER%" (
    echo.
    echo [..] Install / update dependencies dari requirements.txt ...
    "%PYTHON%" -m pip install --upgrade pip
    if errorlevel 1 goto pip_failed
    "%PYTHON%" -m pip install -r "%~dp0requirements.txt"
    if errorlevel 1 goto pip_failed
    >"%MARKER%" echo ok
    echo [OK] Dependencies terinstall.
) else (
    echo [OK] Dependencies sudah terinstall ^(hapus "%MARKER%" untuk re-install^).
)

goto check_env

:pip_failed
echo.
echo [ERROR] pip install gagal. Cek koneksi internet / requirements.txt
pause
exit /b 1

:check_env
REM --- 3) Validasi .env. Kalau belum ada, minta API_ID + API_HASH lalu tulis.
if exist "%~dp0.env" goto run_gui

echo.
echo ----------------------------------------------------
echo File .env belum ada. Mohon isi API_ID dan API_HASH.
echo Dapatkan dari https://my.telegram.org/apps
echo ----------------------------------------------------
set /p APIID=API_ID    : 
set /p APIHASH=API_HASH  : 
if "!APIID!"=="" (
    echo [ERROR] API_ID kosong, tidak bisa lanjut.
    pause
    exit /b 1
)
if "!APIHASH!"=="" (
    echo [ERROR] API_HASH kosong, tidak bisa lanjut.
    pause
    exit /b 1
)
>"%~dp0.env" echo API_ID=!APIID!
>>"%~dp0.env" echo API_HASH=!APIHASH!
echo [OK] .env tersimpan di "%~dp0.env"

:run_gui
echo.
echo ========================================================
echo   Menjalankan GUI ...
echo ========================================================
echo.
"%PYTHON%" "%~dp0gui_app.py"
set "RC=!errorlevel!"

if not "!RC!"=="0" (
    echo.
    echo [ERROR] GUI keluar dengan kode !RC!.
    echo         Pesan error di atas dapat membantu diagnosa.
    pause
)

endlocal
