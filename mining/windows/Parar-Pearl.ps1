param([string]$Python)
$ErrorActionPreference='Stop'
$project=if(Test-Path -LiteralPath (Join-Path $PSScriptRoot 'stop_mining.py')){$PSScriptRoot}else{[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))}
if(-not $Python){Push-Location -LiteralPath $project;try{$Python=(& mise which python).Trim()}finally{Pop-Location}}
& $Python (Join-Path $project 'stop_mining.py') --request-all
if($LASTEXITCODE -ne 0){throw 'Nao foi possivel solicitar parada completa. Confira o painel.'}
