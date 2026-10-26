# Instala componentes para ate seis GPUs NVIDIA sem iniciar mineracao.
$ErrorActionPreference='Stop'
$gpu=(& nvidia-smi -i 0 --query-gpu=name --format=csv,noheader).Trim()
if($LASTEXITCODE -ne 0 -or $gpu -notmatch '(?i)(NVIDIA|GeForce).*(GTX|RTX)'){throw 'Instalacao restrita a GPUs NVIDIA GeForce GTX/RTX.'}
$destination=Join-Path $env:LOCALAPPDATA 'Mining\Pearl'
New-Item -ItemType Directory -Path (Join-Path $destination 'logs') -Force | Out-Null
$manifest=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'downloads.json') -Raw | ConvertFrom-Json
$archive=Join-Path $destination 'SRBMiner-Multi-3-8-0-win64.zip'
if(-not (Test-Path -LiteralPath $archive) -or (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLower() -ne $manifest.srbminer.sha256){
 curl.exe -fL --silent --show-error --max-time 180 -o $archive $manifest.srbminer.url
 if($LASTEXITCODE -ne 0){throw 'Falha no download oficial'}
}
if((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLower() -ne $manifest.srbminer.sha256){throw 'Hash SHA256 divergente; arquivo não será executado'}
if(-not (Test-Path -LiteralPath (Join-Path $destination 'SRBMiner-Multi-3-8-0\SRBMiner-MULTI.exe'))){Expand-Archive -LiteralPath $archive -DestinationPath $destination -Force}
$executable=Join-Path $destination 'SRBMiner-Multi-3-8-0\SRBMiner-MULTI.exe'
if(-not (Test-Path -LiteralPath $executable -PathType Leaf)){throw 'Executavel indisponivel apos extracao. Confira o Historico de protecao do Windows; uma deteccao precisa de analise. No HashPilot, abra Configurar > Seguranca do minerador. Nenhuma excecao foi criada automaticamente.'}
if((Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant() -ne '6eb4e0bc37d65fdf7644ab620737e14f4c7dfa9666acde43d5f38ab1e9094ac1'){throw 'Executavel diferente do hash fixado; instalacao recusada. O hash oficial confirma origem, nao garante seguranca.'}
Get-ChildItem -LiteralPath $PSScriptRoot -File | Where-Object {$_.Extension -in @('.ps1','.cmd','.txt') -and $_.Name -ne 'Instalar-Minerador.ps1'} | ForEach-Object {
 $content=Get-Content -LiteralPath $_.FullName -Raw
 $content=$content.Replace('C:\Users\renan\AppData\Local\Mining\Pearl',$destination)
 if($_.Extension -eq '.cmd'){Set-Content -LiteralPath (Join-Path $destination $_.Name) -Value $content -Encoding ASCII}else{Set-Content -LiteralPath (Join-Path $destination $_.Name) -Value $content -Encoding UTF8}
}
$project=([IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..')))
$runtime=Get-Content -LiteralPath (Join-Path $project 'data\rig-runtime.json') -Raw -Encoding UTF8 | ConvertFrom-Json
foreach($name in $runtime.files){
 if($name -notmatch '^[a-z_]+\.py$'){throw 'Nome de modulo de runtime invalido'}
 Copy-Item -LiteralPath (Join-Path $project $name) -Destination (Join-Path $destination $name) -Force
}
$data=Join-Path $destination 'data'
New-Item -ItemType Directory -Path $data -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $project 'data\nvidia-gpus.json') -Destination (Join-Path $data 'nvidia-gpus.json') -Force
$profile=Join-Path $PSScriptRoot '..\..\portable-data\miner-logs\validated-profile.json'
if((Test-Path -LiteralPath $profile) -and -not(Test-Path -LiteralPath (Join-Path $destination 'logs\validated-profile.json'))){Copy-Item -LiteralPath $profile -Destination (Join-Path $destination 'logs\validated-profile.json')}
Write-Host ('Instalado em '+$destination+'. Inicie pelo INICIAR-PEARL.cmd. Nada foi iniciado automaticamente.')
