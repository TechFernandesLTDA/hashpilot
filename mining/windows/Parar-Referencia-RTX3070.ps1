$ErrorActionPreference='Stop'
$base=$PSScriptRoot
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object {$_.ProcessId -ne $PID -and ($_.CommandLine -like ('*'+$base+'\Ajuste-Fino.ps1*') -or $_.CommandLine -like ('*'+$base+'\Validar-Perfil.ps1*') -or $_.CommandLine -like ('*'+$base+'\Iniciar-Pearl.ps1*'))} | ForEach-Object {Stop-Process -Id $_.ProcessId -Force}
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object {$_.CommandLine -like ('*'+$base+'\Ajustar-RTX3070.ps1*')} | ForEach-Object {Stop-Process -Id $_.ProcessId -Force}
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object {$_.ProcessId -ne $PID -and ($_.CommandLine -like ('*'+$base+'\Maximo-Pearl.ps1*') -or $_.CommandLine -like ('*'+$base+'\Desempenho-Pearl.ps1*'))} | ForEach-Object {Stop-Process -Id $_.ProcessId -Force}
Get-CimInstance Win32_Process -Filter "Name='SRBMiner-MULTI.exe'" | Where-Object {$_.ExecutablePath -eq (Join-Path $base 'SRBMiner-Multi-3-8-0\SRBMiner-MULTI.exe')} | ForEach-Object {Stop-Process -Id $_.ProcessId -Force}
Start-Sleep -Seconds 2
& (Join-Path $base 'Restaurar-GPU.ps1')
& nvidia-smi -i 0 -rgc
& nvidia-smi -i 0 -pl 240
'Mineracao parada. Clock automatico e potencia de fabrica restaurados.' | Set-Content -LiteralPath (Join-Path $base 'logs\stop-status.txt')
