# Регистрация задачи планировщика, под которой работает агент внедрения.
#
# Пятый аудит, пакет C2. Задачу регистрирует ЧЕЛОВЕК с правами
# администратора и делает это один раз: приложение задачу только
# запускает и не может ни создать её, ни переписать. Именно поэтому
# граница держится — процесс приложения, даже скомпрометированный, не
# может перерегистрировать задачу на себя.
#
# Порядок:
#   1) tools\harden-agent-account.ps1  — создать запись и сузить права;
#   2) этот сценарий                   — зарегистрировать задачу;
#   3) в окружении службы задать
#        COSMOS_AGENT_ACCOUNT = LEGION\cosmos-agent
#        COSMOS_AGENT_TASK    = COSMOS-Agent
#      после чего «Реализовать» перестанет требовать подтверждения.
#
# Запускать в консоли АДМИНИСТРАТОРА. Пароль запрашивается интерактивно и
# нигде не сохраняется.

param(
    [string]$TaskName = "COSMOS-Agent",
    [string]$Account  = "$env:COMPUTERNAME\cosmos-agent",
    [string]$TestDir  = "C:\work\Sandbox\Testing\TimeFold\cosmos-test"
)

$ErrorActionPreference = "Stop"

# Отслеживаемый сценарий живёт в tools/ дерева Test; в корень он попадает
# копией отсюда (внешний аудит R6, P0-07: скрипт искал файл в корне,
# которого поставка не создавала, — регистрация падала всегда).
$runner = Join-Path $TestDir "agent-run.cmd"
$tracked = Join-Path $TestDir "tools\agent-run.cmd"
if (-not (Test-Path $runner)) {
    if (-not (Test-Path $tracked)) {
        throw "не найден запускающий сценарий ни в $runner, ни в $tracked"
    }
    Copy-Item $tracked $runner
}
elseif ((Test-Path $tracked) -and
        ((Get-FileHash $runner).Hash -ne (Get-FileHash $tracked).Hash)) {
    throw "agent-run.cmd в корне Test отличается от отслеживаемого tools\agent-run.cmd — сверьте и обновите копию"
}

# Задача НЕ имеет расписания: её запускает приложение через
# Start-ScheduledTask, передав задание файлом в каталоге передачи.
$action  = New-ScheduledTaskAction -Execute $runner -WorkingDirectory $TestDir
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 5) `
    -MultipleInstances IgnoreNew

$cred = Get-Credential -UserName $Account -Message "Пароль учётной записи агента"

Register-ScheduledTask -TaskName $TaskName -Action $action `
    -Settings $settings -User $cred.UserName `
    -Password $cred.GetNetworkCredential().Password -RunLevel Limited -Force | Out-Null

$got = (Get-ScheduledTask -TaskName $TaskName).Principal.UserId
Write-Host "Задача «$TaskName» зарегистрирована от имени: $got"

# Проверка того же, что проверяет посредник: имя должно разрешаться в
# идентификатор безопасности, отличный от записи, под которой работает
# приложение.
$sid = (New-Object System.Security.Principal.NTAccount($got)).Translate(
    [System.Security.Principal.SecurityIdentifier]).Value
Write-Host "Идентификатор безопасности: $sid"
if ($sid -eq ([System.Security.Principal.WindowsIdentity]::GetCurrent()).User.Value) {
    Write-Warning "Это та же запись, под которой запущен текущий сеанс — границы не будет."
}
