# HashPilot Windows bootstrap. Inspect this file before running remote code.
# Public entrypoint: irm https://raw.githubusercontent.com/TechFernandesLTDA/hashpilot/v0.3.7/install.ps1 | iex
[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Programs\HashPilot'),
    [string]$SourcePath,
    [ValidatePattern('^v[0-9]+\.[0-9]+\.[0-9]+$')]
    [string]$Release = 'v0.3.7',
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
    $base = 'https://github.com/TechFernandesLTDA/hashpilot/releases/download/' + $Release + '/'
    $packageName = 'hashpilot-' + $Release + '.zip'
    $archive = Join-Path $stage $packageName
    $sums = Join-Path $stage 'SHA256SUMS'
    Invoke-WebRequest -UseBasicParsing -Uri ($base + 'SHA256SUMS') -OutFile $sums -TimeoutSec 60
    Invoke-WebRequest -UseBasicParsing -Uri ($base + $packageName) -OutFile $archive -TimeoutSec 180
    function Confirm-HashPilotRelease {
    param([string]$Archive,[string]$Sums,[string]$Release,[string]$Destination)
    $packageName = [IO.Path]::GetFileName($Archive)
    $hashes = @{}
    foreach ($line in (Get-Content -LiteralPath $sums -Encoding ASCII)) {
        if ($line -notmatch '^([a-f0-9]{64})  ([^/\\:]+)$' -or $hashes.ContainsKey($Matches[2])) { throw 'Invalid release checksums.' }
        $hashes[$Matches[2]] = $Matches[1]
    }
    if (-not $hashes.ContainsKey($packageName) -or (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $hashes[$packageName]) {
        throw 'Release SHA256 mismatch. No downloaded application code was run.'
    }
    # Inspect names before extraction, including Windows case-insensitive collisions.
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($archive)
    $seen = @{}
    try {
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName
            if ($name -match '(^/|\\|:|(^|/)\.\.?(/|$)|//)' -or $name.EndsWith('/') -or $seen.ContainsKey($name)) { throw 'Unsafe release archive path.' }
            $seen[$name] = $true
        }
        if (($zip.Entries | Measure-Object -Property Length -Sum).Sum -gt 536870912) { throw 'Release archive exceeds size limit.' }
    } finally { $zip.Dispose() }
    $source = $Destination
    Expand-Archive -LiteralPath $archive -DestinationPath $source
    $manifest = Get-Content -LiteralPath (Join-Path $source 'RELEASE-MANIFEST.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $revision = [string]$manifest.commit
    if ($manifest.schema -ne 1 -or $manifest.version -cne $Release -or $revision -cnotmatch '^[a-f0-9]{40}$') { throw 'Invalid release manifest.' }
    $properties = @($manifest.files.PSObject.Properties)
    if ($properties.Count -ne $seen.Count - 1) { throw 'Release inventory mismatch.' }
    foreach ($file in $properties) {
        if (-not $seen.ContainsKey($file.Name) -or $file.Name -ceq 'RELEASE-MANIFEST.json') { throw 'Invalid manifest file.' }
        $path = Join-Path $source $file.Name
        if ((Get-Item -LiteralPath $path).Length -ne $file.Value.size -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $file.Value.sha256) { throw 'Release file verification failed.' }
    }
    return $manifest
    }
    $source = Join-Path $stage 'expanded'
    $verifiedManifest = Confirm-HashPilotRelease -Archive $archive -Sums $sums -Release $Release -Destination $source
    $revision = [string]$verifiedManifest.commit
}
foreach ($required in @('monitor.py','onboarding.py','config.py','contribution.py','rig.py','rig_config.py','rig_controller.py','gpu_telemetry.py','ai_advisor.py','ai_setup.py','rig_panel.py','defender_setup.py','app_update.py','app_version.py','assets\brand\hashpilot.ico','mining\windows\downloads.json','mining\windows\Permissao-Defender.ps1')) {
    if (-not (Test-Path -LiteralPath (Join-Path $source $required) -PathType Leaf)) { throw ('Incomplete package: ' + $required) }
}
function Get-ApplicationFiles {
    param([string]$Root)
    $excluded=@('.git','__pycache__','.venv','.env','settings.json')
    foreach($entry in (Get-ChildItem -LiteralPath $Root -Force | Where-Object { $_.Name -notin $excluded })) {
        if($entry.PSIsContainer) { Get-ChildItem -LiteralPath $entry.FullName -Recurse -File -Force }
        else { $entry }
    }
}
function Test-ApplicationCopy {
    param([string]$Source,[string]$Destination,[array]$Files)
    if(-not (Test-Path -LiteralPath $Destination -PathType Container)){return $false}
    $prefix=$Source.TrimEnd('\','/')+'\'
    $expected=@{}
    foreach($file in $Files){
        $relative=$file.FullName.Substring($prefix.Length)
        $expected[$relative]=$true
        $target=Join-Path $Destination $relative
        if(-not (Test-Path -LiteralPath $target -PathType Leaf)){return $false}
        if((Get-Item -LiteralPath $target).Length -ne $file.Length){return $false}
        if((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash){return $false}
    }
    $destinationPrefix=$Destination.TrimEnd('\','/')+'\'
    foreach($installedFile in (Get-ChildItem -LiteralPath $Destination -Recurse -File -Force)){
        $relative=$installedFile.FullName.Substring($destinationPrefix.Length)
        if(($relative.StartsWith('__pycache__\') -or $relative.Contains('\__pycache__\')) -and $relative.EndsWith('.pyc')){continue}
        if(-not $expected.ContainsKey($relative)){return $false}
    }
    return $true
}
Write-Host '[3/5] Installing application (history and settings are preserved)...'
$versions = Join-Path $InstallRoot 'versions'
New-Item -ItemType Directory -Path $versions -Force | Out-Null
$application = Join-Path $versions $revision
$files=@(Get-ApplicationFiles -Root $source)
if (-not (Test-ApplicationCopy -Source $source -Destination $application -Files $files)) {
    $staged=Join-Path $versions ('stage-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $staged | Out-Null
    try {
        Get-ChildItem -LiteralPath $source -Force | Where-Object { $_.Name -notin @('.git','__pycache__','.venv','.env','settings.json') } | Copy-Item -Destination $staged -Recurse -Force
        if(-not (Test-ApplicationCopy -Source $source -Destination $staged -Files $files)){throw 'Staged application failed verification.'}
        if(Test-Path -LiteralPath $application){
            do {$application=Join-Path $versions ($revision+'-repair-'+[Guid]::NewGuid().ToString('N'))} while(Test-Path -LiteralPath $application)
        }
        Move-Item -LiteralPath $staged -Destination $application
        if(-not (Test-ApplicationCopy -Source $source -Destination $application -Files $files)){throw 'Installed application failed verification.'}
    } finally {
        if(Test-Path -LiteralPath $staged){
            $resolvedStage=[IO.Path]::GetFullPath($staged)
            $resolvedVersions=[IO.Path]::GetFullPath($versions).TrimEnd('\','/')+'\'
            if(-not $resolvedStage.StartsWith($resolvedVersions,[StringComparison]::OrdinalIgnoreCase)){throw 'Unsafe staging cleanup path.'}
            if((Get-Item -LiteralPath $staged).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Unsafe staging link.'}
            Remove-Item -LiteralPath $staged -Recurse -Force
        }
    }
}
$metadata = @{ revision=$revision; release=$(if($SourcePath){'local'}else{$Release}); app=$application; python=$python; installed_at=[DateTime]::UtcNow.ToString('o') }
$metadataPath=Join-Path $InstallRoot 'installation.json'
$metadataTemp=Join-Path $InstallRoot ('installation-'+[Guid]::NewGuid().ToString('N')+'.tmp')
$metadataBackup=Join-Path $InstallRoot ('installation-'+[Guid]::NewGuid().ToString('N')+'.bak')
try {
    [IO.File]::WriteAllText($metadataTemp,($metadata | ConvertTo-Json),[Text.UTF8Encoding]::new($false))
    if(Test-Path -LiteralPath $metadataPath){[IO.File]::Replace($metadataTemp,$metadataPath,$metadataBackup)}
    else {[IO.File]::Move($metadataTemp,$metadataPath)}
} finally {
    if(Test-Path -LiteralPath $metadataTemp){Remove-Item -LiteralPath $metadataTemp -Force}
}
if(Test-Path -LiteralPath $metadataBackup){Remove-Item -LiteralPath $metadataBackup -Force}
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
