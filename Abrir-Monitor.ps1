param([switch]$Resumo, [switch]$Console)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$python=(mise which python).Trim()
$pythonw=Join-Path (Split-Path $python) 'pythonw.exe'
if(-not (Test-Path -LiteralPath $pythonw)){throw 'Python GUI indisponivel no mise'}
$appArgs=@(('"'+(Join-Path $PSScriptRoot 'monitor.py')+'"'))
if($Console){
    $appArgs+='--console'
    Start-Process -FilePath $python -ArgumentList $appArgs -WorkingDirectory $PSScriptRoot -WindowStyle Normal
} else {
    if($Resumo){$appArgs+='--summary'}
    Start-Process -FilePath $pythonw -ArgumentList $appArgs -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
}
