# HashPilot Windows bootstrap. Inspect this file before running remote code.
# Public entrypoint: irm https://raw.githubusercontent.com/TechFernandesLTDA/hashpilot/main/install.ps1 | iex
[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Programs\HashPilot'),
    [string]$SourcePath,
    [switch]$NoLaunch,
    [switch]$NoShortcuts
)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitOperatingSystem) {
    throw 'HashPilot installer requires Windows 10/11 x64.'
}
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
function Find-HashPilotMise {
    $command = Get-Command mise.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidates = @((Join-Path $env:LOCALAPPDATA 'mise\bin\mise.exe'))
    $packages = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    if (Test-Path -LiteralPath $packages) {
        $candidates += @(Get-ChildItem -LiteralPath $packages -Directory -Filter 'jdx.mise_*' | ForEach-Object { Join-Path $_.FullName 'mise\bin\mise.exe' })
    }
    foreach ($candidate in $candidates) { if (Test-Path -LiteralPath $candidate) { return $candidate } }
    return $null
}
Write-Host 'HashPilot | Tech Fernandes' -ForegroundColor Cyan
Write-Host '[1/5] Preparing Python (managed by mise)...'
$miseExe = Find-HashPilotMise
if (-not $miseExe) {
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        throw 'Install App Installer (winget) from Microsoft Store, then run this command again: https://apps.microsoft.com/detail/9nblggh4nns1'
    }
    & winget.exe install --id jdx.mise --exact --source winget --accept-package-agreements --accept-source-agreements --disable-interactivity
    if ($LASTEXITCODE -ne 0) { throw 'Unable to install mise. No application data was changed.' }
    $miseExe = Find-HashPilotMise
    if (-not $miseExe) { throw 'mise was installed but could not be located. Reopen PowerShell and retry.' }
}
# Exact tool request, no changes to global mise config or PowerShell profiles.
& $miseExe install python@3.14.9
if ($LASTEXITCODE -ne 0) { throw 'Python installation failed.' }
$pythonOutput = & $miseExe exec python@3.14.9 -- python -c 'import sys; print(sys.executable)'
$python = ([string]$pythonOutput).Trim()
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $python)) { throw 'Python executable unavailable.' }
& $python -c 'import tkinter, sqlite3; assert tkinter.TkVersion >= 8.6'
if ($LASTEXITCODE -ne 0) { throw 'Python/Tk check failed.' }
$pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) { throw 'Python GUI executable unavailable.' }
Write-Host '[2/5] Downloading HashPilot...'
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
$stage = Join-Path $InstallRoot ('stage-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage | Out-Null
if ($SourcePath) {
    $source = (Resolve-Path -LiteralPath $SourcePath).Path
    $revision = 'local-' + [Guid]::NewGuid().ToString('N')
} else {
    $headers = @{ 'User-Agent' = 'HashPilot-Installer'; 'Accept' = 'application/vnd.github+json' }
    $commit = Invoke-RestMethod -Uri 'https://api.github.com/repos/TechFernandesLTDA/hashpilot/commits/main' -Headers $headers -TimeoutSec 60
    $revision = [string]$commit.sha
    if ($revision -notmatch '^[a-f0-9]{40}$') { throw 'GitHub returned an invalid revision.' }
    $archive = Join-Path $stage 'source.zip'
    Invoke-WebRequest -UseBasicParsing -Uri ('https://codeload.github.com/TechFernandesLTDA/hashpilot/zip/' + $revision) -OutFile $archive -TimeoutSec 180
    Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $stage 'expanded')
    $source = Join-Path $stage ('expanded\hashpilot-' + $revision)
}
foreach ($required in @('monitor.py','onboarding.py','config.py','assets\brand\hashpilot.ico','mining\windows\downloads.json')) {
    if (-not (Test-Path -LiteralPath (Join-Path $source $required))) { throw ('Incomplete package: ' + $required) }
}
Write-Host '[3/5] Installing application (history and settings are preserved)...'
$versions = Join-Path $InstallRoot 'versions'
New-Item -ItemType Directory -Path $versions -Force | Out-Null
$application = Join-Path $versions $revision
if (-not (Test-Path -LiteralPath $application)) {
    New-Item -ItemType Directory -Path $application | Out-Null
    Get-ChildItem -LiteralPath $source -Force | Where-Object { $_.Name -notin @('.git','__pycache__','.venv','.env','settings.json') } | Copy-Item -Destination $application -Recurse -Force
}
$metadata = @{ revision=$revision; app=$application; python=$python; installed_at=[DateTime]::UtcNow.ToString('o') }
$metadata | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $InstallRoot 'installation.json') -Encoding UTF8
Write-Host '[4/5] Creating shortcuts...'
if (-not $NoShortcuts) {
    $shell = New-Object -ComObject WScript.Shell
    $startMenu = Join-Path ([Environment]::GetFolderPath('Programs')) 'HashPilot'
    New-Item -ItemType Directory -Path $startMenu -Force | Out-Null
    foreach ($linkPath in @((Join-Path $startMenu 'HashPilot.lnk'),(Join-Path ([Environment]::GetFolderPath('Desktop')) 'HashPilot.lnk'))) {
        $shortcut = $shell.CreateShortcut($linkPath)
        $shortcut.TargetPath = $pythonw
        $shortcut.Arguments = '"' + (Join-Path $application 'monitor.py') + '"'
        $shortcut.WorkingDirectory = $application
        $shortcut.IconLocation = (Join-Path $application 'assets\brand\hashpilot.ico') + ',0'
        $shortcut.Description = 'HashPilot - Pearl mining, GPU and earnings monitor'
        $shortcut.Save()
    }
}
Write-Host '[5/5] Installation complete.' -ForegroundColor Green
Write-Host ('Version: ' + $revision)
Write-Host ('Application: ' + $application)
Write-Host ('Data: ' + (Join-Path $env:LOCALAPPDATA 'PearlMonitor'))
Write-Host 'First launch lets you keep the author wallet or enter your own. Mining requires your selection in setup.'
if (-not $NoLaunch) {
    $stateDirectory=Join-Path $env:LOCALAPPDATA 'PearlMonitor'
    $snapshotPath=Join-Path $stateDirectory 'latest.json'
    if(Test-Path -LiteralPath $snapshotPath){
        try {
            $running=Get-Content -LiteralPath $snapshotPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $now=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0
            if($running.app.name -like 'HashPilot*Tech Fernandes' -and $now-$running.collected_at -lt 15){
                $request=@{action='exit';at=$now}|ConvertTo-Json
                [IO.File]::WriteAllText((Join-Path $stateDirectory 'ui-request.tmp'),$request,[Text.UTF8Encoding]::new($false))
                Move-Item -LiteralPath (Join-Path $stateDirectory 'ui-request.tmp') -Destination (Join-Path $stateDirectory 'ui-request.json') -Force
                for($wait=0;$wait -lt 60;$wait++){
                    if(-not(Get-Process -Id $running.app.pid -ErrorAction SilentlyContinue)){break}
                    Start-Sleep -Milliseconds 250
                }
                if(Get-Process -Id $running.app.pid -ErrorAction SilentlyContinue){throw 'Close the previous HashPilot using Sair, then open the new shortcut. Mining was not changed.'}
            }
        } catch {throw ('Unable to reopen HashPilot safely: '+$_.Exception.Message)}
    }
    Start-Process -FilePath $pythonw -ArgumentList ('"' + (Join-Path $application 'monitor.py') + '" --setup') -WorkingDirectory $application -WindowStyle Hidden
}
