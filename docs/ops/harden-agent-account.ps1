# Укрепление среды агента внедрения (шаг 0 плана по аудиту 2026-07-26).
#
# ЧТО ДЕЛАЕТ:
#   1. создаёт локальную учётную запись без прав администратора;
#   2. убирает у обычных пользователей право изменять секреты, рабочую
#      базу пользователей и пусковые файлы контуров;
#   3. запрещает агентской учётной записи читать деревья Prod и Stage.
#
# ЧЕГО НЕ ДЕЛАЕТ: не запускает приложение, не трогает Cloudflare, не
# меняет ничего в репозитории. Все действия обратимы, обратные команды —
# в конце файла.
#
# ЗАПУСКАТЬ: от имени администратора, ОДИН РАЗ, на машине legion.
#   powershell -ExecutionPolicy Bypass -File docs\ops\harden-agent-account.ps1
# Сначала посмотреть, что будет сделано, ничего не меняя:
#   ... -File docs\ops\harden-agent-account.ps1 -WhatIf
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$AgentUser = "cosmos-agent",
    # Учётная запись, под которой РЕАЛЬНО работают контуры. Обязательна:
    # см. комментарий в разделе 2 — без явного права приложение теряет
    # доступ к базе пользователей, и вход перестаёт работать у всех.
    [string]$AppUser   = "$env:COMPUTERNAME\lev",
    [string]$ProdDir   = "C:\work\Sandbox\Testing\TimeFold\crew-demo",
    [string]$StageDir  = "C:\work\Sandbox\Testing\TimeFold\cosmos-stage",
    [string]$TestDir   = "C:\work\Sandbox\Testing\TimeFold\cosmos-test"
)

$ErrorActionPreference = "Stop"

function Say($text) { Write-Output "  $text" }

Write-Output "=== Укрепление среды агента внедрения ==="

# --- 1. учётная запись ------------------------------------------------
$existing = Get-LocalUser -Name $AgentUser -ErrorAction SilentlyContinue
if ($existing) {
    Say "учётная запись $AgentUser уже есть — создание пропущено"
} else {
    if ($PSCmdlet.ShouldProcess($AgentUser, "создать локальную учётную запись")) {
        # Пароль запрашивается интерактивно и нигде не сохраняется.
        $pwd = Read-Host -AsSecureString `
            "Задайте пароль для $AgentUser (нужен один раз, для регистрации задачи)"
        # Описание не длиннее 48 знаков — жёсткий предел New-LocalUser.
        # Прежний текст был 51 знак, и скрипт падал на первом же шаге,
        # уже СПРОСИВ пароль (проверено на legion 2026-07-26).
        New-LocalUser -Name $AgentUser -Password $pwd `
            -FullName "COSMOS implement agent" `
            -Description "Непривилегированный агент внедрения COSMOS" `
            -PasswordNeverExpires | Out-Null
        Say "учётная запись $AgentUser создана"
    }
}
# В группу администраторов НЕ добавляем — это и есть смысл шага.
$admins = Get-LocalGroupMember -Group "Administrators" -ErrorAction SilentlyContinue
if ($admins | Where-Object { $_.Name -like "*\$AgentUser" }) {
    Write-Warning ("$AgentUser состоит в группе администраторов. Уберите: " +
                   "Remove-LocalGroupMember -Group Administrators " +
                   "-Member $AgentUser")
}

# --- 2. секреты и рабочее состояние не правит кто попало ---------------
$sensitive = @(
    (Join-Path $ProdDir "webapp\config\claude_api_key"),
    (Join-Path $ProdDir "webapp\config\users.db"),
    (Join-Path $ProdDir "run-webapp.cmd"),
    (Join-Path $StageDir "run-webapp.cmd"),
    (Join-Path $TestDir  "run-webapp.cmd")
)
foreach ($path in $sensitive) {
    if (-not (Test-Path $path)) { Say "нет файла $path — пропуск"; continue }
    if ($PSCmdlet.ShouldProcess($path, "убрать права обычных пользователей")) {
        # ЯВНОЕ право служебной записи — ДО снятия широких групп, иначе
        # приложение теряет доступ. Ловушка неочевидна: служебная запись
        # состоит в группе администраторов, но у НЕПОВЫШЕННОГО процесса
        # эта группа в токене только запрещающая, и весь фактический
        # доступ держится на `Authenticated Users` — ровно на той записи,
        # которую мы убираем. Проверено на legion 2026-07-26: без этой
        # строки приложение перестало бы читать базу пользователей, то
        # есть вход отказал бы всем.
        & icacls $path /grant "${AppUser}:(F)" | Out-Null
        # Наследование прерываем с сохранением текущих записей, затем
        # убираем группы, дающие запись всем локальным пользователям.
        & icacls $path /inheritance:d | Out-Null
        foreach ($who in @("BUILTIN\Users", "NT AUTHORITY\Authenticated Users")) {
            & icacls $path /remove:g $who 2>$null | Out-Null
        }
        & icacls $path /remove:g $AgentUser 2>$null | Out-Null
        Say "права сужены: $path"
    }
}

# --- 3. агент не видит боевые деревья ----------------------------------
foreach ($dir in @($ProdDir, $StageDir)) {
    if (-not (Test-Path $dir)) { Say "нет каталога $dir — пропуск"; continue }
    if ($PSCmdlet.ShouldProcess($dir, "запретить чтение агентской записи")) {
        # (R,W,D,DC): третий аудит 2026-07-26 (§5.5) — запрет только (R,W)
        # не покрывал Delete/DeleteChild, входящие в унаследованный
        # Modify: агент не мог читать боевые файлы, но мог их УДАЛЯТЬ.
        & icacls $dir /deny "${AgentUser}:(OI)(CI)(R,W,D,DC)" /T /C 2>$null | Out-Null
        Say "чтение, запись и удаление запрещены для ${AgentUser}: $dir"
    }
}

# --- 4. агент владеет своим рабочим деревом ----------------------------
if (Test-Path $TestDir) {
    if ($PSCmdlet.ShouldProcess($TestDir, "выдать агенту права на тестовое дерево")) {
        & icacls $TestDir /grant "${AgentUser}:(OI)(CI)F" /T /C 2>$null | Out-Null
        Say "полные права на тестовое дерево выданы ${AgentUser}"
    }
}

$next = @'

Готово. Дальше — два шага вручную:

  1. Зарегистрируйте задачу планировщика, запускающую агента от имени
     учётной записи АГЕНТА (пароль спросят один раз, приложение его не
     хранит):
       schtasks /Create /TN COSMOS-Agent-Implement /RU <АГЕНТ> /RP *
                /SC ONCE /ST 00:00 /TR "<команда запуска агента>"

  2. Объявите учётную запись приложению — в окружении службы:
       COSMOS_AGENT_ACCOUNT=<АГЕНТ>
     Без этой переменной запуск агента внедрения отклоняется.

Проверить результат:
  .venv\Scripts\python.exe -X utf8 -c "from webapp import evolution_runner as e; print(e.isolation_problems() or 'izolyaciya v poryadke')"

ОТКАТ (если понадобится):
  icacls <каталог> /remove:d <АГЕНТ> /T /C
  icacls <файл> /inheritance:e
  Remove-LocalUser -Name <АГЕНТ>
'@
Write-Output $next.Replace("<АГЕНТ>", $AgentUser)
