# Запуск удаления COSMOS.
#
# Этот файл запускается двойным щелчком по УДАЛИТЬ-COSMOS.cmd. Он ищет
# на компьютере Python и передаёт управление uninstall_cosmos.py — тот
# сначала показывает, что удалил бы, и лишь потом спрашивает согласие.
#
# Ничего не удаляется без явного «да» с клавиатуры.

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Find-Python {
    # Настоящий Python, а не заглушка магазина приложений: заглушка
    # лежит в WindowsApps и на запуск отвечает отказом в доступе.
    $candidates = @()
    $cmd = Get-Command python -ErrorAction SilentlyContinue -All
    if ($cmd) { $candidates += $cmd | ForEach-Object { $_.Source } }
    $candidates += Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName }
    $candidates += Get-ChildItem "C:\Program Files\Python3*\python.exe" -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName }
    $candidates += Get-ChildItem "C:\Python3*\python.exe" -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName }
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
Write-Host "  Удаление COSMOS" -ForegroundColor Cyan
Write-Host "  ===============" -ForegroundColor Cyan
Write-Host ""

# --- Python ---------------------------------------------------------------
$py = Find-Python
if (-not $py) {
    Write-Host "  На компьютере не найден Python — программа, на которой"
    Write-Host "  работает удаление. Скорее всего, COSMOS здесь и не"
    Write-Host "  устанавливался: установка без Python невозможна."
    Write-Host ""
    Write-Host "  Если каталоги программы всё же остались, удалите их"
    Write-Host "  вручную: обычно это C:\cosmos и C:\cosmos-data."
    exit 1
}

# --- сам деинсталлятор ----------------------------------------------------
# Он лежит рядом с этим файлом; если комплект принесли без него — берём
# копию из самой установки, она приезжает вместе с исходными текстами.
$script = Join-Path $here "uninstall_cosmos.py"
if (-not (Test-Path $script)) {
    foreach ($guess in @("C:\cosmos\uninstall_cosmos.py",
                         (Join-Path $env:USERPROFILE "cosmos\uninstall_cosmos.py"))) {
        if (Test-Path $guess) { $script = $guess; break }
    }
}
if (-not (Test-Path $script)) {
    Write-Host "  Рядом с этим файлом нет uninstall_cosmos.py, и в обычных"
    Write-Host "  местах установки он тоже не найден. Положите его в эту же"
    Write-Host "  папку и запустите ещё раз."
    exit 1
}

# Рабочий каталог — нейтральный: удалять каталог, который для процесса
# текущий, система не даёт.
Set-Location $env:USERPROFILE

# Деинсталлятор сам покажет, что удалил бы, спросит согласие и выполнит
# удаление вторым проходом с пересчётом всех проверок.
& $py $script @args
exit $LASTEXITCODE
