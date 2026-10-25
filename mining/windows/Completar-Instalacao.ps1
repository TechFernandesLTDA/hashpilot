param(
    [Parameter(Mandatory=$true)][string]$Wallet,
    [Parameter(Mandatory=$true)][string]$Python
)
$ErrorActionPreference='Stop'
try {
    if($Wallet -notmatch '^prl1[ac-hj-np-z02-9]{30,100}$'){throw 'Endereco publico Pearl invalido.'}
    $gpu=(& nvidia-smi -i 0 --query-gpu=name --format=csv,noheader).Trim()
    if($LASTEXITCODE -ne 0 -or $gpu -notmatch '(?i)(NVIDIA|GeForce).*(GTX|RTX)'){throw 'Mineracao restrita a GPUs NVIDIA GeForce GTX/RTX detectadas pelo driver.'}
    if(Get-Process -Name SRBMiner-MULTI -ErrorAction SilentlyContinue){
        Write-Host 'Mineracao ja ativa: nenhum processo, clock ou carteira foi alterado. Para trocar a carteira do minerador, pare-o e inicie novamente com a configuracao desejada.' -ForegroundColor Yellow
    } else {
        & (Join-Path $PSScriptRoot 'Instalar-Minerador.ps1')
        if($LASTEXITCODE -ne 0){throw 'Falha na instalacao do SRBMiner.'}
        $starter=Join-Path $env:LOCALAPPDATA 'Mining\Pearl\Iniciar-Pearl.ps1'
        & $starter -Wallet $Wallet -Python $Python
    }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    Read-Host 'Pressione Enter para fechar'
    exit 1
}
