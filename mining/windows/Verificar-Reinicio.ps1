$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
& (Join-Path $PSScriptRoot 'Parar-Pearl.ps1')
& nvidia-smi -i 0 --query-gpu=power.limit,power.draw,temperature.gpu,clocks.gr,clocks.mem,fan.speed --format=csv | Set-Content -LiteralPath 'logs\stop-verification.csv'
Start-Process -FilePath 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -WindowStyle Hidden -ArgumentList ('-NoProfile -ExecutionPolicy Bypass -File '+(Join-Path $PSScriptRoot 'Iniciar-Pearl.ps1')) -PassThru | Select-Object Id | ConvertTo-Json | Set-Content -LiteralPath 'logs\restart-process.json'
