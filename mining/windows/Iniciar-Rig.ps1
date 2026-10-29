param([string]$Python,[switch]$MigrarExistente,[string]$LegacyUuid,[switch]$ReferenciaRTX3070)
$ErrorActionPreference='Stop'
$state=Join-Path $env:LOCALAPPDATA 'PearlMonitor'
$base=Join-Path $env:LOCALAPPDATA 'Mining\Pearl'
$settingsPath=Join-Path $state 'settings.json'
$logs=Join-Path $base 'logs'
if(Test-Path -LiteralPath $settingsPath){$settings=Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json;if($settings.log_dir){$logs=[Environment]::ExpandEnvironmentVariables([string]$settings.log_dir)}}
$project=if(Test-Path -LiteralPath (Join-Path $PSScriptRoot 'rig.py')){$PSScriptRoot}else{[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))}
if(-not $Python){Push-Location -LiteralPath $project;try{$Python=(& mise which python).Trim()}finally{Pop-Location}}
if(-not (Test-Path -LiteralPath $Python)){throw 'Python gerenciado pelo mise indisponivel.'}
if($LegacyUuid -and $LegacyUuid -notmatch '^GPU-[A-Za-z0-9-]{1,100}$'){throw 'UUID NVIDIA invalido.'}
$principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){
 $arguments='-NoProfile -ExecutionPolicy Bypass -File "'+$PSCommandPath+'" -Python "'+$Python+'"'
 if($MigrarExistente){$arguments+=' -MigrarExistente'}
 if($LegacyUuid){$arguments+=' -LegacyUuid '+$LegacyUuid}
 if($ReferenciaRTX3070){$arguments+=' -ReferenciaRTX3070'}
 Start-Process -FilePath (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden
 exit
}
$script=Join-Path $project 'rig.py'
$miner=(& $Python (Join-Path $project 'miner_update.py') --path).Trim()
if($LASTEXITCODE -ne 0){throw 'Minerador selecionado nao passou na verificacao; confira a Central de operacao.'}
if(-not (Test-Path -LiteralPath $script) -or -not (Test-Path -LiteralPath $miner)){throw 'Reinstale o minerador pelo assistente antes de iniciar o rig.'}
New-Item -ItemType Directory -Path $state,$logs -Force | Out-Null
$registry=Join-Path $state 'rig-manager.json'
if(Test-Path -LiteralPath $registry){
 $running=Get-Content -LiteralPath $registry -Raw -Encoding UTF8 | ConvertFrom-Json
 if($running.pid){
  $process=Get-CimInstance Win32_Process -Filter ('ProcessId='+[int]$running.pid) -ErrorAction SilentlyContinue
  if($process -and $process.CommandLine -like ('*'+$script+'*') -and -not $MigrarExistente){
   $queue=Join-Path $state 'rig-requests'
   New-Item -ItemType Directory -Path $queue -Force | Out-Null
   $request=@{id=[Guid]::NewGuid().ToString('N');action='reconcile';at=([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0)}
   $temporary=Join-Path $queue ($request.id+'.tmp')
   [IO.File]::WriteAllText($temporary,($request|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
   Move-Item -LiteralPath $temporary -Destination (Join-Path $queue ($request.id+'.json'))
   Write-Host 'Servico do rig ja ativo; configuracao sera reconciliada.'
   exit
  }
 }
}
$arguments=@(('"'+$script+'"'),'--serve','--state-dir',('"'+$state+'"'),'--log-dir',('"'+$logs+'"'),'--settings',('"'+$settingsPath+'"'),'--miner-path',('"'+$miner+'"'),'--python',('"'+$Python+'"'),'--adopt-legacy')
if($MigrarExistente){$arguments+='--migrate-legacy'}
if($LegacyUuid){$arguments+=@('--legacy-uuid',$LegacyUuid)}
if($ReferenciaRTX3070){$arguments+='--reference-rtx3070'}
$stamp=Get-Date -Format 'yyyyMMdd-HHmmss'
$process=Start-Process -FilePath $Python -ArgumentList $arguments -WorkingDirectory $project -WindowStyle Hidden -RedirectStandardOutput (Join-Path $state ('rig-service-'+$stamp+'.out.log')) -RedirectStandardError (Join-Path $state ('rig-service-'+$stamp+'.err.log')) -PassThru
Write-Host ('Servico do rig iniciado. PID '+$process.Id+'. Os estados e erros individuais aparecem no HashPilot.')
