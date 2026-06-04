@echo off
title GemmaAI - Build Installer
chcp 65001 >nul 2>&1

echo.
echo  ==========================================
echo       GemmaAI - Build Setup.exe
echo  ==========================================
echo.

:: cd ไปที่โฟลเดอร์ของ build.bat เสมอ (build_system/)
cd /d "%~dp0"

:: ── ตรวจสอบ Python ──────────────────────────────────────
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] ไม่พบ Python
    echo  ดาวน์โหลดได้ที่: https://www.python.org/downloads/
    pause & exit /b 1
)

for /f "tokens=*" %%i in ('python --version 2^>^&1') do set PY_VER=%%i
echo  [OK] %PY_VER%

:: ── ตรวจสอบว่า build.py อยู่ในโฟลเดอร์เดียวกัน ─────────
if not exist "build.py" (
    echo  [ERROR] Not found build.py in this folder
    echo  Check if build.bat and build.py are in the same build_system/ folder
    pause & exit /b 1
)

:: ── ตรวจสอบว่า setup_wizard.py อยู่ใน parent folder ─────
if not exist "..\setup_wizard.py" (
    echo  [ERROR] Not found setup_wizard.py in %~dp0..
    echo  Correct directory structure:
    echo    final_package\
    echo    ^|-- build_system\   ^<-- This folder
    echo    ^|   ^|-- build.bat
    echo    ^|   ^`-- build.py
    echo    ^`-- setup_wizard.py
    echo    ^`-- main.py ...
    pause & exit /b 1
)

echo  [OK] Directory structure is correct

:: ── ติดตั้ง build tools ──────────────────────────────────
echo.
echo  Installing build tools (pyinstaller, pillow)...
pip install pyinstaller pillow -q --no-warn-script-location
if %errorlevel% neq 0 (
    echo  [WARN] pip install มีปัญหา — ลอง build ต่อ...
)

:: ── Run build ────────────────────────────────────────────
echo.
echo  Building...
echo.
python build.py

if %errorlevel% neq 0 (
    echo.
    echo  ==========================================
    echo   [ERROR] Build Fail
    echo   Please check error above and fix it
    echo  ==========================================
    pause & exit /b 1
)

echo.
echo  ==========================================
echo   Done! Check folder  build_system\dist\
echo  ==========================================
echo.
pause