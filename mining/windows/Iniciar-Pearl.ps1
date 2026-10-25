param([string]$Wallet='prl1pl2km7u7uddqqfmry4px9c9c7lpfeskhtl0yvkylvgupcjkafxmaqc6cr8t', [string]$Python)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$monitorState=Join-Path $env:LOCALAPPDATA 'PearlMonitor'
$settingsPath=Join-Path $monitorState 'settings.json'
$settings=$null
if(Test-Path -LiteralPath $settingsPath){
 $settings=Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
 if(-not $PSBoundParameters.ContainsKey('Wallet') -and $settings.wallet){$Wallet=[string]$settings.wallet}
}
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
$supportProcess=$null
try {
 New-Item -ItemType Directory -Path $logDir -Force | Out-Null
 $pool='prl.kryptex.network:7048'
 $authorWallet=(& $Python -c 'from contribution import AUTHOR_WALLET; print(AUTHOR_WALLET)').Trim()
 if($LASTEXITCODE -ne 0){throw 'Modulo de contribuicao nao instalado. Reinstale pelo assistente.'}
 $supportEnabled=($settings -and $settings.support_enabled -is [bool] -and $settings.support_enabled -eq $true -and $settings.wallet -eq $Wallet -and $Wallet -ne $authorWallet)
 if($supportEnabled){
  Write-Host 'Ajuda opcional pela ferramenta: 5% do tempo para o autor (57 min / 3 min). Desative em Configurar.' -ForegroundColor Cyan
  $supportScript=Join-Path $PSScriptRoot 'contribution.py'
  $proxyArgs=@(('"'+$supportScript+'"'),'--wallet',$Wallet,'--settings',('"'+$settingsPath+'"'),'--state-dir',('"'+$logDir+'"'),'--port','0')
  $supportProcess=Start-Process -FilePath $Python -ArgumentList $proxyArgs -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru
  $proxyReady=$false
  for($attempt=0;$attempt -lt 60;$attempt++){
   $supportProcess.Refresh()
   if($supportProcess.HasExited){throw 'Proxy de contribuicao encerrou antes da inicializacao.'}
   $statusPath=Join-Path $logDir 'support-mining.json'
   if(Test-Path -LiteralPath $statusPath){
    try {
     $proxy=Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
     if($proxy.pid -eq $supportProcess.Id -and $proxy.ready -and $proxy.port -gt 0){$pool='127.0.0.1:'+ $proxy.port;$proxyReady=$true;break}
    } catch {}
   }
   Start-Sleep -Milliseconds 250
  }
  if(-not $proxyReady){throw 'Proxy local indisponivel. Mineracao nao foi iniciada.'}
 }
 # Start from the board's current driver settings. No RTX 3070 clocks are copied to other models.
 $arguments=@('--disable-cpu','--disable-msr-tweaks','--algorithm-gpu','pearlhash','--gpu-id','0','--pool',$pool,'--wallet',$Wallet,'--worker','HashPilotGPU0','--gpu-off-temperature','83','--retry-time','1','--log-file',('"'+$log+'"'))
 $minerProcess=Start-Process -FilePath $miner -ArgumentList $arguments -WorkingDirectory (Split-Path $miner) -WindowStyle Hidden -PassThru
 $minerProcess.Id|Set-Content -LiteralPath (Join-Path $logDir 'adaptive-miner.pid')
 Start-Sleep -Seconds 5
 $minerProcess.Refresh()
 if($minerProcess.HasExited){throw 'Minerador encerrou durante inicializacao'}
 $controlArgs=@($controller,'--control','--miner-pid',$minerProcess.Id,'--index','0','--duration','300','--state-dir',$logDir,'--monitor-state-dir',$monitorState)
 if($settings.ai_enabled){$controlArgs+='--ollama'}
 & $Python @controlArgs
 if($LASTEXITCODE -ne 0){throw 'Controlador termico falhou; mineracao sera encerrada e GPU restaurada'}
} finally {
 if($minerProcess){$minerProcess.Refresh();if(-not $minerProcess.HasExited){Stop-Process -Id $minerProcess.Id -Force}}
 if($supportProcess){$supportProcess.Refresh();if(-not $supportProcess.HasExited){Stop-Process -Id $supportProcess.Id -Force}}
 # The exclusive controller restores its own clock/power state. The reference
 # RTX-only reset script must never be applied to another GPU model.
}
