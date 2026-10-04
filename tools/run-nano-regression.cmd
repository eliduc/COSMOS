@echo off
rem Ночная регрессия «Полярис-нано» полным циклом (пирамида, п.3).
rem Регистрируется задачей планировщика на площадке (03:30):
rem   schtasks /Create /TN "COSMOS-nano-regression" /SC DAILY
rem     /ST 03:30 /TR "C:\work\...\crew-demo\tools\run-nano-regression.cmd"
rem Отчёты: <data_root>\regression\<дата>\report.md
cd /d "%~dp0.."
".venv\Scripts\python.exe" "tools\nano_regression.py" %*
