# Execute apenas em Windows com a RTX 3070 correspondente ao perfil de referência.
$ErrorActionPreference='Stop'
$gpu=(& nvidia-smi -i 0 --query-gpu=name --format=csv,noheader).Trim()
if($gpu -notmatch 'RTX 3070$'){throw 'Este perfil foi validado somente na RTX 3070. Não aplique em outra GPU.'}
$destination=Join-Path $env:LOCALAPPDATA 'Mining\Pearl'
New-Item -ItemType Directory -Path (Join-Path $destination 'logs') -Force | Out-Null
$manifest=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'downloads.json') -Raw | ConvertFrom-Json
$archive=Join-Path $destination 'SRBMiner-Multi-3-8-0-win64.zip'
curl.exe -fL --silent --show-error --max-time 180 -o $archive $manifest.srbminer.url
if($LASTEXITCODE -ne 0){throw 'Falha no download oficial'}
if((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLower() -ne $manifest.srbminer.sha256){throw 'Hash SHA256 divergente; arquivo não será executado'}
Expand-Archive -LiteralPath $archive -DestinationPath $destination -Force
Get-ChildItem -LiteralPath $PSScriptRoot -File | Where-Object {$_.Extension -in @('.ps1','.cmd','.txt') -and $_.Name -ne 'Instalar-Minerador.ps1'} | ForEach-Object {
 $content=Get-Content -LiteralPath $_.FullName -Raw
 $content=$content.Replace('C:\Users\renan\AppData\Local\Mining\Pearl',$destination)
 if($_.Extension -eq '.cmd'){Set-Content -LiteralPath (Join-Path $destination $_.Name) -Value $content -Encoding ASCII}else{Set-Content -LiteralPath (Join-Path $destination $_.Name) -Value $content -Encoding UTF8}
}
$project=([IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..')))
foreach($name in @('thermal.py','thermal_controller.py')){Copy-Item -LiteralPath (Join-Path $project $name) -Destination (Join-Path $destination $name) -Force}
$profile=Join-Path $PSScriptRoot '..\..\portable-data\miner-logs\validated-profile.json'
if(Test-Path -LiteralPath $profile){Copy-Item -LiteralPath $profile -Destination (Join-Path $destination 'logs\validated-profile.json') -Force}
Write-Host ('Instalado em '+$destination+'. Inicie pelo INICIAR-PEARL.cmd. Nada foi iniciado automaticamente.')
