param([string]$Python,[Parameter(Mandatory=$true)][string]$GpuUuids)
$ErrorActionPreference='Stop'
$ids=@($GpuUuids.Split(',') | Select-Object -Unique)
if($ids.Count -lt 1 -or $ids.Count -gt 6){throw 'Selecione de uma a seis GPUs'}
foreach($id in $ids){if($id -notmatch '^GPU-[A-Za-z0-9-]{1,100}$'){throw 'UUID NVIDIA invalido'}}
$project=if(Test-Path -LiteralPath (Join-Path $PSScriptRoot 'rig.py')){$PSScriptRoot}else{[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))}
if(-not $Python){Push-Location -LiteralPath $project;try{$Python=(& mise which python).Trim()}finally{Pop-Location}}
if(-not(Test-Path -LiteralPath $Python)){throw 'Python indisponivel'}
$principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){
    $arguments='-NoProfile -ExecutionPolicy Bypass -File "'+$PSCommandPath+'" -Python "'+$Python+'" -GpuUuids '+($ids -join ',')
    try{
        Start-Process -FilePath (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -ErrorAction Stop | Out-Null
    }catch{
        # The non-elevated caller can report a denied UAC without changing driver state.
        & $Python (Join-Path $project 'stop_ui.py') --denied --uuids ($ids -join ',')
        throw
    }
    exit
}
$helper=Join-Path $project 'stop_mining.py'
$arguments=@($helper)
foreach($id in $ids){$arguments+=@('--uuid',$id)}
& $Python @arguments
if($LASTEXITCODE -ne 0){throw 'Restauração incompleta; confira o resultado por GPU no painel'}
