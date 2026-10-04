@echo off
rem Udalenie COSMOS odnim dvoynym shchelchkom. Vse soobshcheniya - v okne
rem PowerShell na yazyke, vybrannom pri ustanovke. Etot fayl namerenno
rem soderzhit tolko latinitsu: cmd chitaet .cmd v sistemnoy kodovoy
rem stranitse i lomaet kirillitsu.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall_bootstrap.ps1"
pause
