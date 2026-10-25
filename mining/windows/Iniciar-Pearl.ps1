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
if($LASTEXITCODE -ne 0 -or $gpu -notmatch '(?i)(NVIDIA|GeForce).*(GTX|RTX)'){throw 'Este iniciador aceita somente GPUs NVIDIA GeForce GTX/RTX detectadas pelo driver.'}
if(-not $Python){$Python=(& mise which python).Trim()}
if(-not (Test-Path -LiteralPath $Python)){throw 'Python do mise indisponivel'}
$controller=Join-Path $PSScriptRoot 'gpu_agent.py'
if(-not (Test-Path -LiteralPath $controller)){throw 'Controlador termico nao instalado'}
$miner=Join-Path $PSScriptRoot 'SRBMiner-Multi-3-8-0\SRBMiner-MULTI.exe'
$logDir=Join-Path $PSScriptRoot 'logs'
$log=Join-Path $logDir ('mining-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.log')
$minerProcess=$null
try {
 # Start from the board's current driver settings. No RTX 3070 clocks are copied to other models.
 $arguments=@('--disable-cpu','--disable-msr-tweaks','--algorithm-gpu','pearlhash','--gpu-id','0','--pool','prl.kryptex.network:7048','--wallet',$Wallet,'--worker','HashPilotGPU0','--gpu-off-temperature','83','--log-file',('"'+$log+'"'))
 $minerProcess=Start-Process -FilePath $miner -ArgumentList $arguments -WorkingDirectory (Split-Path $miner) -WindowStyle Hidden -PassThru
 $minerProcess.Id|Set-Content -LiteralPath (Join-Path $logDir 'adaptive-miner.pid')
 Start-Sleep -Seconds 5
 $minerProcess.Refresh()
 if($minerProcess.HasExited){throw 'Minerador encerrou durante inicializacao'}
 $monitorState=Join-Path $env:LOCALAPPDATA 'PearlMonitor'
 $controlArgs=@($controller,'--control','--miner-pid',$minerProcess.Id,'--index','0','--duration','300','--state-dir',$logDir,'--monitor-state-dir',$monitorState)
 $settingsPath=Join-Path $monitorState 'settings.json'
 if(Test-Path -LiteralPath $settingsPath){$settings=Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json;if($settings.ai_enabled){$controlArgs+='--ollama'}}
 & $Python @controlArgs
 if($LASTEXITCODE -ne 0){throw 'Controlador termico falhou; mineracao sera encerrada e GPU restaurada'}
} finally {
 if($minerProcess){$minerProcess.Refresh();if(-not $minerProcess.HasExited){Stop-Process -Id $minerProcess.Id -Force}}
 # The exclusive controller restores its own clock/power state. The reference
 # RTX-only reset script must never be applied to another GPU model.
}
