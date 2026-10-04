@echo off
rem Helper script launched by start_timefold.bat in a separate window.

set CONDA_ROOT=C:\ProgramData\anaconda3

title PANORAMA COS server

call "%CONDA_ROOT%\Scripts\activate.bat" TimeFold
if errorlevel 1 (
    echo.
    echo [error] Failed to activate conda env TimeFold.
    echo.
    pause
    exit /b 1
)

cd /d "%~dp0"

echo.
echo ================================================
echo   PANORAMA COS backend
echo   Press Ctrl+C in this window to stop.
echo ================================================
echo.

crew-demo

echo.
echo Server stopped.
pause
