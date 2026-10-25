param(
    [Parameter(Mandatory=$true)][string]$Wallet,
    [Parameter(Mandatory=$true)][string]$Python
)
$ErrorActionPreference='Stop'
try {
    if($Wallet -notmatch '^prl1[ac-hj-np-z02-9]{30,100}$'){throw 'Endereco publico Pearl invalido.'}
    $gpu=(& nvidia-smi -i 0 --query-gpu=name --format=csv,noheader).Trim()
    if($LASTEXITCODE -ne 0 -or $gpu -notmatch '(?i)(NVIDIA|GeForce).*(GTX|RTX)'){throw 'Mineracao restrita a GPUs NVIDIA GeForce GTX/RTX detectadas pelo driver.'}
    & (Join-Path $PSScriptRoot 'Instalar-Minerador.ps1')
    if($LASTEXITCODE -ne 0){throw 'Falha na instalacao do SRBMiner.'}
    & (Join-Path $env:LOCALAPPDATA 'Mining\Pearl\Iniciar-Rig.ps1') -Python $Python
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    Read-Host 'Pressione Enter para fechar'
    exit 1
}
