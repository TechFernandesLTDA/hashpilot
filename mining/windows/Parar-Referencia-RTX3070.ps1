$ErrorActionPreference='Stop'
$base=$PSScriptRoot
. (Join-Path $PSScriptRoot 'Referencia-Comum.ps1')
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object {$_.ProcessId -ne $PID -and ($_.CommandLine -like ('*'+$base+'\Ajuste-Fino.ps1*') -or $_.CommandLine -like ('*'+$base+'\Validar-Perfil.ps1*') -or $_.CommandLine -like ('*'+$base+'\Iniciar-Pearl.ps1*'))} | ForEach-Object {Stop-Process -Id $_.ProcessId -Force}
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object {$_.CommandLine -like ('*'+$base+'\Ajustar-RTX3070.ps1*')} | ForEach-Object {Stop-Process -Id $_.ProcessId -Force}
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object {$_.ProcessId -ne $PID -and ($_.CommandLine -like ('*'+$base+'\Maximo-Pearl.ps1*') -or $_.CommandLine -like ('*'+$base+'\Desempenho-Pearl.ps1*'))} | ForEach-Object {Stop-Process -Id $_.ProcessId -Force}
# Encerra so o executavel exato da versao selecionada (sem curinga), como antes com o caminho fixo 3.8.0.
$referenceMiner=$null
try{$referenceMiner=Get-ReferenceMinerPath $base}catch{Write-Host $_.Exception.Message}
if($referenceMiner){Get-CimInstance Win32_Process -Filter "Name='SRBMiner-MULTI.exe'" | Where-Object {$_.ExecutablePath -eq $referenceMiner} | ForEach-Object {Stop-Process -Id $_.ProcessId -Force}}
Start-Sleep -Seconds 2
& (Join-Path $base 'Restaurar-GPU.ps1')
& nvidia-smi -i 0 -rgc
& nvidia-smi -i 0 -pl 240
'Mineracao parada. Clock automatico e potencia de fabrica restaurados.' | Set-Content -LiteralPath (Join-Path $base 'logs\stop-status.txt')
