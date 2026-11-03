# Analisa a sintaxe de todos os .ps1 versionados, sem executá-los.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$failed = $false
$count = 0
foreach ($name in (git -C $root ls-files '*.ps1')) {
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile((Join-Path $root $name), [ref]$null, [ref]$errors)
    $count++
    if ($errors) {
        $failed = $true
        foreach ($item in $errors) { Write-Host "${name}:$($item.Extent.StartLineNumber): $($item.Message)" }
    }
}
Write-Host "PowerShell: $count arquivos analisados"
if ($failed) { exit 1 }
