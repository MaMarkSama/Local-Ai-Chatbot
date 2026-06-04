@echo off
title Gemma AI Launcher

echo.
echo  ==========================================
echo       Gemma AI - Starting...
echo  ==========================================
echo.


cd /d "%~dp0"

:: Check Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] Python not found.
    echo  Download: https://www.python.org/downloads/
    pause
    exit /b 1
)

:: Install deps if needed
python -c "import fastapi, uvicorn, httpx, linebot, dotenv, pypdf, docx, openpyxl, multipart" >nul 2>&1
if %errorlevel% neq 0 (
    echo  Installing dependencies...
    pip install -r requirements.txt -q --no-warn-script-location
)

:: Launch
if exist "launcher.py" (
    python launcher.py
) else (
    echo  [ERROR] launcher.py not found. Run install.bat first.
    pause
    exit /b 1
)
