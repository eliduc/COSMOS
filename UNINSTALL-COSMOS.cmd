@echo off
rem Same as UDALIT-COSMOS.cmd, ASCII name for non-Russian sites.
rem Udalenie COSMOS odnim dvoynym shchelchkom.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall_bootstrap.ps1"
pause
