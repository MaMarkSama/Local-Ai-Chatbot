@echo off
chcp 65001 >nul
title Gemma AI Launcher

color 0B
echo.
echo  ======================================================
echo.
echo     ██████╗ ███████╗███╗   ███╗███╗   ███╗ █████╗ 
echo    ██╔════╝ ██╔════╝████╗ ████║████╗ ████║██╔══██╗
echo    ██║  ███╗█████╗  ██╔████╔██║██╔████╔██║███████║
echo    ██║   ██║██╔══╝  ██║╚██╔╝██║██║╚██╔╝██║██╔══██║
echo    ╚██████╔╝███████╗██║ ╚═╝ ██║██║ ╚═╝ ██║██║  ██║
echo     ╚═════╝ ╚══════╝╚═╝     ╚═╝╚═╝     ╚═╝╚═╝  ╚═╝
echo.
echo               Local AI Chatbot Assistant
echo  ======================================================
echo.

cd /d "%~dp0"

:: Check Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    color 0C
    echo  [ X ] Error: Python not found.
    echo  Please install Python from: https://www.python.org/downloads/
    pause
    exit /b 1
)

:: Install deps if needed
python -c "import fastapi, uvicorn, httpx, linebot, dotenv, pypdf, docx, openpyxl, multipart, duckduckgo_search" >nul 2>&1
if %errorlevel% neq 0 (
    color 0E
    echo  [ * ] Installing required dependencies...
    pip install -r requirements.txt -q --no-warn-script-location
)

:: Launch
if exist "launcher.py" (
    color 0A
    echo  [ + ] Launching Gemma AI UI...
    python launcher.py
) else (
    color 0C
    echo  [ X ] Error: launcher.py not found! Please run install.bat first.
    pause
    exit /b 1
)
