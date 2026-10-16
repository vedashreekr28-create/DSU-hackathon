@echo off
setlocal
echo ========================================================
echo Starting EcoTwin Campus Smart Waste Digital Twin Server
echo ========================================================

set "PY_EXE="
if exist "D:\DSU-hackathon\.venv\Scripts\python.exe" (
    set "PY_EXE=D:\DSU-hackathon\.venv\Scripts\python.exe"
) else if exist "%~dp0.venv\Scripts\python.exe" (
    set "PY_EXE=%~dp0.venv\Scripts\python.exe"
) else (
    set "PY_EXE=python"
)

cd /d "%~dp0ecotwin\src"
echo Using Python: %PY_EXE%
echo.
echo Application will be running at:
echo    http://127.0.0.1:5000
echo    http://localhost:5000
echo.
echo Press Ctrl+C in this window to stop the server.
echo ========================================================
echo.

"%PY_EXE%" app.py
pause
