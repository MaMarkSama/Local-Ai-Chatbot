@echo off
title GemmaAI - Build Installer

echo.
echo  ==========================================
echo       GemmaAI - Build Setup.exe
echo  ==========================================
echo.

cd /d "%~dp0"

:: Check Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] Python not found.
    pause & exit /b 1
)

:: Install build tools
echo  Installing build tools...
pip install pyinstaller pillow -q

:: Run build script
echo  Building...
python build.py

if %errorlevel% neq 0 (
    echo  [ERROR] Build failed.
    pause & exit /b 1
)

echo.
echo  Done! Check dist\ folder.
pause
