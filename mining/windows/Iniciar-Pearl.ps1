param([string]$Wallet='prl1pl2km7u7uddqqfmry4px9c9c7lpfeskhtl0yvkylvgupcjkafxmaqc6cr8t')
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
if($Wallet -notmatch '^prl1[ac-hj-np-z02-9]{30,100}$'){throw 'Endereco publico Pearl invalido'}
$principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){
 Start-Process -FilePath 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -Verb RunAs -WindowStyle Hidden -ArgumentList ('-NoProfile -ExecutionPolicy Bypass -File '+$PSCommandPath)
 exit
}
if(Get-Process -Name SRBMiner-MULTI -ErrorAction SilentlyContinue){'Minerador ja esta ativo.';exit}
$clock=1650
$profile=Join-Path $PSScriptRoot 'logs\validated-profile.json'
if(Test-Path -LiteralPath $profile){$data=Get-Content -LiteralPath $profile -Raw|ConvertFrom-Json;if($data.CoreClock -in @(1650,1710,1740)){$clock=[int]$data.CoreClock}}
$miner=Join-Path $PSScriptRoot 'SRBMiner-Multi-3-8-0\SRBMiner-MULTI.exe'
$log=Join-Path $PSScriptRoot ('logs\mining-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.log')
try {
 & $miner --disable-cpu --disable-msr-tweaks --algorithm-gpu pearlhash --gpu-id 0 --pool prl.kryptex.network:7048 --wallet $Wallet --worker RenanRTX3070 --gpu-off-temperature 78 --gpu-plimit0 240 --gpu-cclock0 $clock --gpu-coffset0 150 --gpu-mclock0 5001 --gpu-fan0 100 --log-file $log
} finally {
 & (Join-Path $PSScriptRoot 'Restaurar-GPU.ps1')
}
