@echo off
title Gemma AI - Installer
cd /d "%~dp0"

:: ป้องกันรันซ้ำด้วย lock file
if exist ".setup_running" (
    echo Setup is already running. Please check your taskbar.
    pause
    exit /b 0
)

:: สร้าง lock file
echo running > .setup_running

:: Check Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found.
    echo Download: https://www.python.org/downloads/
    del .setup_running >nul 2>&1
    pause
    exit /b 1
)

echo [OK] Python found.
echo Installing minimum requirement...
python -m pip install python-dotenv -q --no-warn-script-location 2>nul

echo Opening Setup Wizard...
python setup_wizard.py

:: ลบ lock file เมื่อ wizard ปิดแล้ว
del .setup_running >nul 2>&1