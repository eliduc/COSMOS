@echo off
rem Start the Kotlin Timefold Quarkus service on http://127.0.0.1:9091.
rem The Python FastAPI server (port 8765) calls this when the user picks
rem the "Timefold Kotlin 2.0" engine in the settings dropdown.

set CONDA_ROOT=C:\ProgramData\anaconda3
title PANORAMA COS - Timefold Kotlin solver

call "%CONDA_ROOT%\Scripts\activate.bat" TimeFold
if errorlevel 1 (
    echo.
    echo [error] Failed to activate conda env TimeFold.
    pause
    exit /b 1
)

cd /d "%~dp0"

echo.
echo ================================================
echo   Timefold Kotlin 2.0 (Quarkus)  -  port 9091
echo   Press Ctrl+C in this window to stop.
echo ================================================
echo.

mvn -B quarkus:dev -Dquarkus.console.disable-input=true

echo.
echo Kotlin solver stopped.
pause
