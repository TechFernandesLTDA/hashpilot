param([string]$Python)
$ErrorActionPreference='Stop'
$state=Join-Path $env:LOCALAPPDATA 'PearlMonitor'
$project=if(Test-Path -LiteralPath (Join-Path $PSScriptRoot 'rig.py')){$PSScriptRoot}else{[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))}
if(-not $Python){Push-Location -LiteralPath $project;try{$Python=(& mise which python).Trim()}finally{Pop-Location}}
if(-not (Test-Path -LiteralPath $Python)){throw 'Python indisponivel.'}
# pythonw.exe has no console stdout: reading miner_update.py --path from it returns nothing
# and the start aborts after the previous service was already stopped. Use the console python.exe.
if([IO.Path]::GetFileName($Python) -ieq 'pythonw.exe'){$console=Join-Path (Split-Path -Parent $Python) 'python.exe';if(Test-Path -LiteralPath $console){$Python=$console}}
$principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){
    # No process is stopped until the Windows administrator prompt is accepted.
    $arguments='-NoProfile -ExecutionPolicy Bypass -File "'+$PSCommandPath+'" -Python "'+$Python+'"'
    Start-Process -FilePath (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden
    exit
}
$motorFile=Join-Path $state 'rig-motor.txt'
$rustExe=Join-Path $project 'rust\target\release\hashpilot.exe'
# The running service locks rust\target\release\hashpilot.exe: Windows refuses to overwrite a
# running executable. Build into a staging target and swap the file only after the shutdown.
$stagingTarget=Join-Path $project 'rust\target\atualizacao'
$stagedExe=Join-Path $stagingTarget 'release\hashpilot.exe'
$rustMotor=(Test-Path -LiteralPath $motorFile) -and ((Get-Content -LiteralPath $motorFile -Raw).Trim() -ieq 'Rust')
if($rustMotor){
    # Recompila antes de parar o serviço: se falhar, a sessão atual continua.
    Push-Location -LiteralPath (Join-Path $project 'rust')
    try{& mise exec -- cargo build --release -q -p hashpilot-cli --target-dir $stagingTarget;if($LASTEXITCODE -ne 0){throw 'Compilacao do servico em Rust falhou. A sessao atual foi preservada.'}}finally{Pop-Location}
}
$registry=Join-Path $state 'rig-manager.json'
$referenceRtx3070=$false
if(Test-Path -LiteralPath $registry){
    $running=Get-Content -LiteralPath $registry -Raw -Encoding UTF8 | ConvertFrom-Json
    $referenceRtx3070=($running.marker -eq 'HashPilotRig-v1' -and $running.reference_uuid -eq 'GPU-bafb0ad5-dd9e-ee1d-a0c0-221c0dfbfc75')
    $now=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0
    $process=if($running.pid){Get-CimInstance Win32_Process -Filter ('ProcessId='+[int]$running.pid) -ErrorAction SilentlyContinue}else{$null}
    if($process){
        $stateArg='(?i)(?:^|\s)--state-dir\s+(?:"'+[regex]::Escape($state)+'"|'+[regex]::Escape($state)+')(?:\s|$)'
        if($running.marker -ne 'HashPilotRig-v1' -or $now-$running.sampled_at -lt 0 -or $now-$running.sampled_at -gt 15 -or
           -not (([IO.Path]::GetFileName($process.ExecutablePath) -in @('python.exe','pythonw.exe') -and $process.CommandLine -match '(?i)[\\/]rig\.py"?(?:\s|$)') -or
                 ($process.ExecutablePath -ieq $rustExe -and $process.CommandLine -match '(?i)\srig\s.*--serve')) -or
           $process.CommandLine -notmatch $stateArg){
            throw 'Identidade do gerenciador nao confirmada. A sessao atual foi preservada.'
        }
        $created=$process.CreationDate
        $queue=Join-Path $state 'rig-requests'
        New-Item -ItemType Directory -Path $queue -Force | Out-Null
        $request=@{id=[Guid]::NewGuid().ToString('N');action='shutdown';at=$now}
        $temporary=Join-Path $queue ($request.id+'.tmp')
        [IO.File]::WriteAllText($temporary,($request|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $temporary -Destination (Join-Path $queue ($request.id+'.json'))
        for($attempt=0;$attempt -lt 120;$attempt++){
            $current=Get-CimInstance Win32_Process -Filter ('ProcessId='+[int]$running.pid) -ErrorAction SilentlyContinue
            if(-not $current -or $current.CreationDate -ne $created){break}
            Start-Sleep -Milliseconds 250
        }
        if($current -and $current.CreationDate -eq $created){throw 'Servico ainda esta encerrando. Nenhum processo foi finalizado a forca; verifique o estado antes de tentar novamente.'}
    }
}
if($rustMotor){
    # Service stopped (or not running): the old executable is free to be replaced.
    $copied=$false
    for($attempt=0;$attempt -lt 40 -and -not $copied;$attempt++){
        try{Copy-Item -LiteralPath $stagedExe -Destination $rustExe -Force;$copied=$true}catch{Start-Sleep -Milliseconds 250}
    }
    if(-not $copied){throw 'Nao foi possivel substituir rust\target\release\hashpilot.exe (arquivo em uso). O servico esta parado; rode Iniciar-Rig.ps1 para voltar.'}
}
$startArguments=@{Python=$Python}
if($referenceRtx3070){$startArguments.ReferenciaRTX3070=$true;$startArguments.LegacyUuid='GPU-bafb0ad5-dd9e-ee1d-a0c0-221c0dfbfc75'}
& (Join-Path $PSScriptRoot 'Iniciar-Rig.ps1') @startArguments
