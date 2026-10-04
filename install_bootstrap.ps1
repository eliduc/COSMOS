# Подготовка компьютера и запуск установщика COSMOS.
#
# Этот файл запускается двойным щелчком по УСТАНОВИТЬ-COSMOS.cmd.
# Он проверяет, есть ли на компьютере Python и git, при их отсутствии
# предлагает поставить их штатным магазином приложений Windows (winget),
# и затем передаёт управление установщику install_cosmos.py — тот сам
# обследует компьютер и задаст несколько простых вопросов.
#
# Ничего не устанавливается без явного согласия («да» с клавиатуры).

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Ask-Yes([string]$question) {
    $answer = Read-Host ("  " + $question + " (да/нет)")
    return $answer -match '^(да|д|yes|y|1)$'
}

function Find-Python {
    # Настоящий Python, а не заглушка магазина приложений: заглушка
    # лежит в WindowsApps и на запуск отвечает отказом в доступе.
    $candidates = @()
    $cmd = Get-Command python -ErrorAction SilentlyContinue -All
    if ($cmd) { $candidates += $cmd | ForEach-Object { $_.Source } }
    $candidates += Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName }
    $candidates += Get-ChildItem "C:\Program Files\Python3*\python.exe" -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName }
    foreach ($p in $candidates) {
        if ($p -like "*WindowsApps*") { continue }
        try {
            $v = & $p -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($v -match '^3\.(1[1-4])$') { return $p }
        } catch { }
    }
    return $null
}

Write-Host ""
Write-Host "  Установка COSMOS" -ForegroundColor Cyan
Write-Host "  ================" -ForegroundColor Cyan
Write-Host ""

# --- 1. Python ------------------------------------------------------------
$py = Find-Python
if (-not $py) {
    Write-Host "  На компьютере не найден Python (программа, которая нужна"
    Write-Host "  для работы установщика). Его можно поставить автоматически."
    if (-not (Ask-Yes "Поставить Python сейчас?")) {
        Write-Host "  Без Python установка невозможна. Ничего не изменено."
        exit 1
    }
    winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    # winget обновляет PATH только для новых окон — ищем по известным местам
    $py = Find-Python
    if (-not $py) {
        Write-Host "  Python поставился, но не находится. Закройте это окно,"
        Write-Host "  запустите УСТАНОВИТЬ-COSMOS.cmd ещё раз."
        exit 1
    }
}
Write-Host ("  Python найден: " + $py)

# --- 2. git -----------------------------------------------------------------
$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) {
    Write-Host ""
    Write-Host "  Не найден git (программа, которой скачиваются исходные"
    Write-Host "  тексты COSMOS). Его можно поставить автоматически."
    if (-not (Ask-Yes "Поставить git сейчас?")) {
        Write-Host "  Без git установка невозможна. Ничего не изменено."
        exit 1
    }
    winget install --id Git.Git --silent --accept-package-agreements --accept-source-agreements
    $env:PATH = "$env:ProgramFiles\Git\cmd;" + $env:PATH
    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) {
        Write-Host "  Git поставился, но не находится. Закройте это окно,"
        Write-Host "  запустите УСТАНОВИТЬ-COSMOS.cmd ещё раз."
        exit 1
    }
}
Write-Host ("  Git найден: " + $git.Source)

# --- 3. Установщик ----------------------------------------------------------
$installer = Join-Path $here "install_cosmos.py"
if (-not (Test-Path $installer)) {
    Write-Host "  Рядом с этим файлом нет install_cosmos.py — положите оба"
    Write-Host "  файла в один каталог и запустите заново."
    exit 1
}
Write-Host ""
Write-Host "  Дальше установщик сам осмотрит компьютер и задаст несколько"
Write-Host "  простых вопросов. Отвечайте «да», «нет» или выбирайте из"
Write-Host "  предложенных вариантов; у каждого вопроса есть безопасный"
Write-Host "  ответ по умолчанию — можно просто нажимать Enter."
Write-Host ""

& $py $installer
exit $LASTEXITCODE
