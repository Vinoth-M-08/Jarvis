@echo off
title J.A.R.V.I.S.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [!] The Python environment .venv is missing. Follow "Setup" in README.md first.
    pause
    exit /b 1
)

rem The local brain (local mode, fast mode, backup) needs Ollama running.
tasklist /FI "IMAGENAME eq ollama.exe" 2>nul | find /I "ollama.exe" >nul
if errorlevel 1 (
    if exist "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe" (
        echo Starting Ollama...
        start "" "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe" --hide
    ) else (
        echo [i] Ollama isn't installed - the local brain won't work, Claude still will.
    )
)

set PYTHONIOENCODING=utf-8
echo Starting Jarvis - loading the speech and AI libraries takes about a minute...
echo (Ctrl+C stops Jarvis; if Windows asks "Terminate batch job", answer Y.)
".venv\Scripts\python.exe" -m jarvis.main
if errorlevel 1 (
    echo.
    echo [!] Jarvis stopped with an error. The details are above, and in data\logs\jarvis.log
)
pause
