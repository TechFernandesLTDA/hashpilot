$ErrorActionPreference='Stop'
$principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){
 Start-Process -FilePath (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') -Verb RunAs -WindowStyle Hidden -ArgumentList ('-NoProfile -ExecutionPolicy Bypass -File "'+$PSCommandPath+'"')
 exit
}
$minerPath=Join-Path $PSScriptRoot 'SRBMiner-Multi-3-8-0\SRBMiner-MULTI.exe'
Get-CimInstance Win32_Process -Filter "Name='SRBMiner-MULTI.exe'" | Where-Object {$_.ExecutablePath -eq $minerPath} | ForEach-Object {Stop-Process -Id $_.ProcessId -Force}
# The exclusive supervisor notices its miner exiting, restores its own initial
# power limit and releases the clock. Never apply a fixed RTX limit to another GPU.
Write-Host 'Minerador desta instalacao encerrado. Aguarde a restauracao pelo supervisor NVIDIA.'