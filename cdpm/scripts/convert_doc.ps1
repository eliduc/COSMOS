# Convert a legacy .doc file to .docx via Microsoft Word COM automation.
# ASCII-only on purpose: Cyrillic in PowerShell one-liners and scripts
# has burned this project twice (encoding class "print kills process").
# Called by cdpm/freeze.py; not intended for manual use.
param(
    [Parameter(Mandatory = $true)][string]$In,
    [Parameter(Mandatory = $true)][string]$Out
)
$ErrorActionPreference = "Stop"
$word = $null
try {
    $word = New-Object -ComObject Word.Application
} catch {
    Write-Output "NO-WORD"
    exit 3
}
try {
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $inPath = (Resolve-Path -LiteralPath $In).Path
    $outDir = Split-Path -Parent $Out
    if ($outDir -and -not (Test-Path -LiteralPath $outDir)) {
        New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    }
    # Open(FileName, ConfirmConversions:=false, ReadOnly:=true)
    $doc = $word.Documents.Open($inPath, $false, $true)
    # 12 = wdFormatXMLDocument (.docx)
    $doc.SaveAs2([string]$Out, 12)
    $doc.Close($false)
    Write-Output "OK"
    exit 0
} catch {
    Write-Output ("FAIL: " + $_.Exception.Message)
    exit 1
} finally {
    if ($null -ne $word) { $word.Quit() }
}
