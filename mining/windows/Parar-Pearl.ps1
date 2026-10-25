$ErrorActionPreference='Stop'
$state=Join-Path $env:LOCALAPPDATA 'PearlMonitor'
$registry=Join-Path $state 'rig-manager.json'
if(-not (Test-Path -LiteralPath $registry)){throw 'Servico do rig nao esta ativo. Os processos externos nao serao encerrados.'}
$status=Get-Content -LiteralPath $registry -Raw -Encoding UTF8 | ConvertFrom-Json
if(-not $status.pid -or -not (Get-Process -Id $status.pid -ErrorAction SilentlyContinue)){throw 'Servico do rig nao esta ativo.'}
$queue=Join-Path $state 'rig-requests'
New-Item -ItemType Directory -Path $queue -Force | Out-Null
$request=@{id=[Guid]::NewGuid().ToString('N');action='shutdown';at=([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0)}
$temporary=Join-Path $queue ($request.id+'.tmp')
[IO.File]::WriteAllText($temporary,($request|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
Move-Item -LiteralPath $temporary -Destination (Join-Path $queue ($request.id+'.json'))
Write-Host 'Parada solicitada. Cada supervisor restaura somente a sua GPU; o painel e o historico continuam ativos.'