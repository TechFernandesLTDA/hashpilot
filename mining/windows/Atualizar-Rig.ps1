param([string]$Python)
$ErrorActionPreference='Stop'
$state=Join-Path $env:LOCALAPPDATA 'PearlMonitor'
$project=if(Test-Path -LiteralPath (Join-Path $PSScriptRoot 'rig.py')){$PSScriptRoot}else{[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))}
if(-not $Python){Push-Location -LiteralPath $project;try{$Python=(& mise which python).Trim()}finally{Pop-Location}}
if(-not (Test-Path -LiteralPath $Python)){throw 'Python indisponivel.'}
$principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){
    # No process is stopped until the Windows administrator prompt is accepted.
    $arguments='-NoProfile -ExecutionPolicy Bypass -File "'+$PSCommandPath+'" -Python "'+$Python+'"'
    Start-Process -FilePath (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden
    exit
}
$registry=Join-Path $state 'rig-manager.json'
if(Test-Path -LiteralPath $registry){
    $running=Get-Content -LiteralPath $registry -Raw -Encoding UTF8 | ConvertFrom-Json
    $now=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0
    $process=if($running.pid){Get-CimInstance Win32_Process -Filter ('ProcessId='+[int]$running.pid) -ErrorAction SilentlyContinue}else{$null}
    if($process){
        $stateArg='(?i)(?:^|\s)--state-dir\s+(?:"'+[regex]::Escape($state)+'"|'+[regex]::Escape($state)+')(?:\s|$)'
        if($running.marker -ne 'HashPilotRig-v1' -or $now-$running.sampled_at -lt 0 -or $now-$running.sampled_at -gt 15 -or
           [IO.Path]::GetFileName($process.ExecutablePath) -notin @('python.exe','pythonw.exe') -or
           $process.CommandLine -notmatch '(?i)[\\/]rig\.py"?(?:\s|$)' -or $process.CommandLine -notmatch $stateArg){
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
& (Join-Path $PSScriptRoot 'Iniciar-Rig.ps1') -Python $Python
