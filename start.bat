@echo off
title Gemma AI Launcher

echo.
echo  ==========================================
echo       Gemma AI - Starting...
echo  ==========================================
echo.

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] Python not found. Please install Python first.
    echo  Download: https://www.python.org/downloads/
    pause
    exit /b 1
)

cd /d "%~dp0"

echo  Checking dependencies...
python -c "import fastapi, uvicorn, httpx, linebot, dotenv, pypdf, docx, openpyxl, multipart" >nul 2>&1
if %errorlevel% neq 0 (
    echo  Installing dependencies...
    pip install -r requirements.txt -q
    echo  Done.
)

if exist "launcher.py" (
    echo  Opening Gemma AI Launcher...
    python launcher.py
) else if exist "run.py" (
    echo  Starting server...
    python run.py
    pause
) else (
    echo  [ERROR] launcher.py or run.py not found.
    pause
    exit /b 1
)
