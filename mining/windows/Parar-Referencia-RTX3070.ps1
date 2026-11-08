$ErrorActionPreference='Stop'
$base=$PSScriptRoot
. (Join-Path $PSScriptRoot 'Referencia-Comum.ps1')
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object {$_.ProcessId -ne $PID -and ($_.CommandLine -like ('*'+$base+'\Ajuste-Fino.ps1*') -or $_.CommandLine -like ('*'+$base+'\Validar-Perfil.ps1*') -or $_.CommandLine -like ('*'+$base+'\Iniciar-Pearl.ps1*'))} | ForEach-Object {Stop-Process -Id $_.ProcessId -Force}
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object {$_.CommandLine -like ('*'+$base+'\Ajustar-RTX3070.ps1*')} | ForEach-Object {Stop-Process -Id $_.ProcessId -Force}
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object {$_.ProcessId -ne $PID -and ($_.CommandLine -like ('*'+$base+'\Maximo-Pearl.ps1*') -or $_.CommandLine -like ('*'+$base+'\Desempenho-Pearl.ps1*'))} | ForEach-Object {Stop-Process -Id $_.ProcessId -Force}
# Encerra os executaveis exatos do fluxo de referencia (sem curinga): a versao selecionada agora
# e os caminhos anotados ao iniciar, mesmo que a selecao tenha mudado depois.
$referenceMiners=@(Get-ReferenceMinerPaths $base)
Get-CimInstance Win32_Process -Filter "Name='SRBMiner-MULTI.exe'" | Where-Object {$referenceMiners -contains $_.ExecutablePath} | ForEach-Object {Stop-Process -Id $_.ProcessId -Force}
Remove-Item -LiteralPath (Join-Path $base $ReferenceMinerRecord) -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
& (Join-Path $base 'Restaurar-GPU.ps1')
& nvidia-smi -i 0 -rgc
& nvidia-smi -i 0 -pl 240
'Mineracao parada. Clock automatico e potencia de fabrica restaurados.' | Set-Content -LiteralPath (Join-Path $base 'logs\stop-status.txt')
