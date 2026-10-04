@echo off
rem Запускающий сценарий агента внедрения. Исполняется ЗАДАЧЕЙ
rem планировщика от имени отдельной учётной записи (пакет C2 пятого
rem аудита); приложение его напрямую не вызывает.
rem
rem Договор с посредником (webapp/agent_broker.py):
rem   agent-handoff\current-run.txt   — ТОЧНЫЙ идентификатор прогона
rem                                     (пишет посредник перед запуском
rem                                     задачи; внешний аудит R7, P0-06в:
rem                                     «самая свежая папка» допускала
rem                                     подмену соседним каталогом);
rem   agent-handoff\<run_id>\task.txt — задание (пишет приложение);
rem   agent-handoff\<run_id>\pid.txt  — номер ЭТОГО процесса (пишем мы);
rem   agent-handoff\<run_id>\out.txt  — поток вывода агента;
rem   agent-handoff\<run_id>\done.txt — код завершения.
rem
rem Посредник сверяет владельца процесса из pid.txt. Это процесс cmd.exe
rem самого сценария; агент запускается его потомком и наследует тот же
rem маркер доступа, поэтому проверка одного процесса покрывает и агента.

setlocal enabledelayedexpansion
set "HANDOFF=%COSMOS_AGENT_HANDOFF%"
if "%HANDOFF%"=="" set "HANDOFF=%~dp0agent-handoff"
if "%COSMOS_AGENT_CLI%"=="" set "COSMOS_AGENT_CLI=claude"

rem Точный прогон — из указателя посредника; «самых свежих папок» нет.
set "RUN_ID="
if exist "%HANDOFF%\current-run.txt" (
    set /p RUN_ID=<"%HANDOFF%\current-run.txt"
)
if not defined RUN_ID (
    echo NO CURRENT RUN POINTER 1>&2
    exit /b 2
)
set "RUN=%HANDOFF%\%RUN_ID%"
if not exist "%RUN%\task.txt" (
    echo NO TASK FOR RUN %RUN_ID% 1>&2
    exit /b 2
)

rem Собственный номер процесса: у порождённого powershell берём номер
rem родителя, то есть этого cmd.exe.
powershell -NoProfile -NonInteractive -Command "$me=(Get-CimInstance Win32_Process -Filter ('ProcessId='+$PID)).ParentProcessId; Set-Content -LiteralPath (Join-Path $env:RUN 'pid.txt') -Value $me -Encoding ascii"

rem Режим задания (пакет C, аудит R10 P0-04): exec.ps1 — проверка
rem кандидата (pytest/замер) под записью агента; иначе — прежний запуск
rem клиента модели с заданием из task.txt.
if exist "%RUN%\exec.ps1" goto exec

rem Те же флаги, что у прямого запуска (внешний аудит R7, P0-06в): без -p
rem CLI открывал интерактивную сессию, а без запрета инструментов ожидания
rem агент уходил «ждать уведомлений» и умирал с сессией (находка 27).
"%COSMOS_AGENT_CLI%" -p --output-format stream-json --verbose ^
    --dangerously-skip-permissions ^
    --disallowedTools "Monitor ScheduleWakeup" ^
    < "%RUN%\task.txt" > "%RUN%\out.txt" 2>&1
set "RC=%ERRORLEVEL%"
goto finish

:exec
powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass ^
    -File "%RUN%\exec.ps1" > "%RUN%\out.txt" 2>&1
set "RC=%ERRORLEVEL%"

:finish
echo %RC% > "%RUN%\done.txt"
exit /b %RC%
