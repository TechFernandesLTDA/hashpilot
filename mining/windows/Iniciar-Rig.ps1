param([string]$Python,[switch]$MigrarExistente,[string]$LegacyUuid,[switch]$ReferenciaRTX3070,[ValidateSet('','Python','Rust')][string]$Motor)
$ErrorActionPreference='Stop'
$state=Join-Path $env:LOCALAPPDATA 'PearlMonitor'
$base=Join-Path $env:LOCALAPPDATA 'Mining\Pearl'
$settingsPath=Join-Path $state 'settings.json'
$logs=Join-Path $base 'logs'
if(Test-Path -LiteralPath $settingsPath){$settings=Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json;if($settings.log_dir){$logs=[Environment]::ExpandEnvironmentVariables([string]$settings.log_dir)}}
$project=if(Test-Path -LiteralPath (Join-Path $PSScriptRoot 'rig.py')){$PSScriptRoot}else{[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))}
if(-not $Python){Push-Location -LiteralPath $project;try{$Python=(& mise which python).Trim()}finally{Pop-Location}}
if(-not (Test-Path -LiteralPath $Python)){throw 'Python gerenciado pelo mise indisponivel.'}
# pythonw.exe has no console stdout: reading miner_update.py --path from it returns nothing
# and the start aborts after the previous service was already stopped. Use the console python.exe.
if([IO.Path]::GetFileName($Python) -ieq 'pythonw.exe'){$console=Join-Path (Split-Path -Parent $Python) 'python.exe';if(Test-Path -LiteralPath $console){$Python=$console}}
if($LegacyUuid -and $LegacyUuid -notmatch '^GPU-[A-Za-z0-9-]{1,100}$'){throw 'UUID NVIDIA invalido.'}
# Motor do serviço: Python (rig.py) ou Rust (hashpilot rig --serve). A escolha fica em rig-motor.txt,
# para o Atualizar-Rig.ps1 reiniciar no mesmo motor.
$motorFile=Join-Path $state 'rig-motor.txt'
if(-not $Motor){$Motor=if((Test-Path -LiteralPath $motorFile) -and ((Get-Content -LiteralPath $motorFile -Raw).Trim() -ieq 'Rust')){'Rust'}else{'Python'}}
$rustExe=Join-Path $project 'rust\target\release\hashpilot.exe'
$principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){
 $arguments='-NoProfile -ExecutionPolicy Bypass -File "'+$PSCommandPath+'" -Python "'+$Python+'"'
 if($MigrarExistente){$arguments+=' -MigrarExistente'}
 if($LegacyUuid){$arguments+=' -LegacyUuid '+$LegacyUuid}
 if($ReferenciaRTX3070){$arguments+=' -ReferenciaRTX3070'}
 $arguments+=' -Motor '+$Motor
 Start-Process -FilePath (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden
 exit
}
$script=Join-Path $project 'rig.py'
$miner=(& $Python (Join-Path $project 'miner_update.py') --path).Trim()
if($LASTEXITCODE -ne 0){throw 'Minerador selecionado nao passou na verificacao; confira a Central de operacao.'}
if(-not (Test-Path -LiteralPath $script) -or -not (Test-Path -LiteralPath $miner)){throw 'Reinstale o minerador pelo assistente antes de iniciar o rig.'}
if($Motor -eq 'Rust' -and -not (Test-Path -LiteralPath $rustExe)){throw 'Servico em Rust nao compilado: rode cargo build --release -p hashpilot-cli na pasta rust.'}
New-Item -ItemType Directory -Path $state,$logs -Force | Out-Null
$registry=Join-Path $state 'rig-manager.json'
if(Test-Path -LiteralPath $registry){
 $running=Get-Content -LiteralPath $registry -Raw -Encoding UTF8 | ConvertFrom-Json
 if($running.pid){
  $process=Get-CimInstance Win32_Process -Filter ('ProcessId='+[int]$running.pid) -ErrorAction SilentlyContinue
  $pythonService=$process -and $process.CommandLine -like ('*'+$script+'*')
  $rustService=$process -and $process.ExecutablePath -ieq $rustExe -and $process.CommandLine -match '(?i)\srig\s.*--serve'
  $sameMotor=($Motor -eq 'Python' -and $pythonService) -or ($Motor -eq 'Rust' -and $rustService)
  if(($pythonService -or $rustService) -and -not $sameMotor){
   # O outro motor está ativo: desligamento limpo (para e restaura as GPUs) antes de iniciar este.
   $created=$process.CreationDate
   $queue=Join-Path $state 'rig-requests'
   New-Item -ItemType Directory -Path $queue -Force | Out-Null
   $request=@{id=[Guid]::NewGuid().ToString('N');action='shutdown';at=([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0)}
   $temporary=Join-Path $queue ($request.id+'.tmp')
   [IO.File]::WriteAllText($temporary,($request|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
   Move-Item -LiteralPath $temporary -Destination (Join-Path $queue ($request.id+'.json'))
   for($attempt=0;$attempt -lt 240;$attempt++){
    $current=Get-CimInstance Win32_Process -Filter ('ProcessId='+[int]$running.pid) -ErrorAction SilentlyContinue
    if(-not $current -or $current.CreationDate -ne $created){break}
    Start-Sleep -Milliseconds 250
   }
   if($current -and $current.CreationDate -eq $created){throw 'Servico anterior ainda esta encerrando. Nenhum processo foi finalizado a forca.'}
  }elseif($sameMotor -and -not $MigrarExistente){
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
[IO.File]::WriteAllText($motorFile,$Motor,[Text.UTF8Encoding]::new($false))
if($Motor -eq 'Rust'){
 # Mesmos argumentos do rig.py; o Rust ainda inicia gpu_agent.py e contribution.py com este Python.
 $arguments=@('rig')+$arguments[1..($arguments.Count-1)]+@('--project-dir',('"'+$project+'"'))
 $executable=$rustExe
}else{$executable=$Python}
$stamp=Get-Date -Format 'yyyyMMdd-HHmmss'
$process=Start-Process -FilePath $executable -ArgumentList $arguments -WorkingDirectory $project -WindowStyle Hidden -RedirectStandardOutput (Join-Path $state ('rig-service-'+$stamp+'.out.log')) -RedirectStandardError (Join-Path $state ('rig-service-'+$stamp+'.err.log')) -PassThru
Write-Host ('Servico do rig ('+$Motor+') iniciado. PID '+$process.Id+'. Os estados e erros individuais aparecem no HashPilot.')
