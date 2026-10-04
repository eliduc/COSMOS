@echo off
rem Launch the two servers needed for the 4-engine PANORAMA COS:
rem   - Kotlin Timefold 2.0 (port 9091)  — "timefold_kotlin" engine
rem   - Python FastAPI      (port 8765)  — UI + ortools + timefold_python + solverforge
rem
rem Each server opens in its own window so you can stop them individually
rem with Ctrl+C. Close this launcher window — the children keep running.

set HERE=%~dp0
title PANORAMA COS - launcher

echo.
echo Starting Kotlin solver (port 9091)...
start "PANORAMA Kotlin solver" cmd /k "%HERE%kotlin-solver\run.bat"

rem Small stagger so the JVM doesn't fight Python for disk on cold boot.
timeout /t 3 > nul

echo Starting Python COS (port 8765)...
start "PANORAMA Python COS" cmd /k "%HERE%run_server.bat"

echo.
echo Both servers launched in separate windows.
echo Open http://127.0.0.1:8765/ in your browser.
echo.
pause
