@echo off
rem Ustanovka COSMOS odnim dvoynym shchelchkom. Vse soobshcheniya - v okne
rem PowerShell na russkom. Etot fayl namerenno soderzhit tolko latinitsu:
rem cmd chitaet .cmd v sistemnoy kodovoy stranitse i lomaet kirillitsu.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_bootstrap.ps1"
pause
