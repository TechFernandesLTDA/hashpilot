param([string]$Wallet='prl1pl2km7u7uddqqfmry4px9c9c7lpfeskhtl0yvkylvgupcjkafxmaqc6cr8t', [string]$Python)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
if($Wallet -notmatch '^prl1[ac-hj-np-z02-9]{30,100}$'){throw 'Endereco publico Pearl invalido'}
$principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){
 $elevatedArgs='-NoProfile -ExecutionPolicy Bypass -File "'+$PSCommandPath+'" -Wallet '+$Wallet
 if($Python){$elevatedArgs+=' -Python "'+$Python+'"'}
 Start-Process -FilePath (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') -Verb RunAs -WindowStyle Hidden -ArgumentList $elevatedArgs
 exit
}
if(Get-Process -Name SRBMiner-MULTI -ErrorAction SilentlyContinue){'Minerador ja esta ativo.';exit}
$gpu=(& nvidia-smi -i 0 --query-gpu=name --format=csv,noheader).Trim()
if($LASTEXITCODE -ne 0 -or $gpu -notmatch 'RTX 3070$'){throw 'Controlador validado somente na RTX 3070'}
if(-not $Python){$Python=(& mise which python).Trim()}
if(-not (Test-Path -LiteralPath $Python)){throw 'Python do mise indisponivel'}
$controller=Join-Path $PSScriptRoot 'thermal_controller.py'
if(-not (Test-Path -LiteralPath $controller)){throw 'Controlador termico nao instalado'}
$clock=1740
$miner=Join-Path $PSScriptRoot 'SRBMiner-Multi-3-8-0\SRBMiner-MULTI.exe'
$logDir=Join-Path $PSScriptRoot 'logs'
$log=Join-Path $logDir ('mining-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.log')
$minerProcess=$null
try {
 $arguments=@('--disable-cpu','--disable-msr-tweaks','--algorithm-gpu','pearlhash','--gpu-id','0','--pool','prl.kryptex.network:7048','--wallet',$Wallet,'--worker','RenanRTX3070','--gpu-off-temperature','84','--gpu-plimit0','240','--gpu-cclock0',$clock,'--gpu-coffset0','150','--gpu-mclock0','5001','--gpu-fan0','100','--log-file',('"'+$log+'"'))
 $minerProcess=Start-Process -FilePath $miner -ArgumentList $arguments -WorkingDirectory (Split-Path $miner) -WindowStyle Hidden -PassThru
 $minerProcess.Id|Set-Content -LiteralPath (Join-Path $logDir 'adaptive-miner.pid')
 Start-Sleep -Seconds 5
 $minerProcess.Refresh()
 if($minerProcess.HasExited){throw 'Minerador encerrou durante inicializacao'}
 & $Python $controller --miner-pid $minerProcess.Id --initial-clock $clock --status-dir $logDir
 if($LASTEXITCODE -ne 0){throw 'Controlador termico falhou; mineracao sera encerrada e GPU restaurada'}
} finally {
 if($minerProcess){$minerProcess.Refresh();if(-not $minerProcess.HasExited){Stop-Process -Id $minerProcess.Id -Force}}
 & (Join-Path $PSScriptRoot 'Restaurar-GPU.ps1')
}
