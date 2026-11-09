"""Exercise service handoff with fake processes in a temporary AppData only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from testing_support import windows_only

class ServiceUpdateTests(unittest.TestCase):
    @windows_only("powershell.exe")
    def test_guarded_service_handoff_never_forces_a_process_or_changes_real_appdata(self):
        source=(Path(__file__).parent/'mining/windows/Atualizar-Rig.ps1').read_text(encoding='utf-8')
        # Mock the privilege context only in this isolated fixture. Production
        # still requires real Windows UAC before any shutdown request is written.
        source=source.replace("$principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())","$principal=$null")
        source=source.replace("if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){","if($false){")
        for scenario,ok in [('graceful',True),('reference',True),('absent_reference',True),('absent',True),('foreign',False),('stale',False),('timeout',False)]:
            with self.subTest(scenario=scenario),tempfile.TemporaryDirectory() as d:
                root=Path(d);state=root/'PearlMonitor';state.mkdir()
                (root/'update.ps1').write_text(source,encoding='utf-8-sig')
                (root/'Iniciar-Rig.ps1').write_text("param([string]$Python,[switch]$ReferenciaRTX3070,[string]$LegacyUuid)\nSet-Content -LiteralPath (Join-Path $PSScriptRoot 'started.txt') -Value (@{reference=$ReferenciaRTX3070.IsPresent;legacy_uuid=$LegacyUuid}|ConvertTo-Json -Compress)",encoding='utf-8')
                runner=r'''
$ErrorActionPreference='Stop'
$state=Join-Path $env:LOCALAPPDATA 'PearlMonitor'
$now=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0
if($env:AUDIT_CASE -eq 'stale'){$now-=3600}
$reference=if($env:AUDIT_CASE -in @('reference','absent_reference')){'GPU-bafb0ad5-dd9e-ee1d-a0c0-221c0dfbfc75'}else{$null}
@{pid=42;marker='HashPilotRig-v1';sampled_at=$now;reference_uuid=$reference}|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $state 'rig-manager.json')
$script:queries=0
function Get-CimInstance {
    param($ClassName,$Filter,$ErrorAction)
    $script:queries++
    if($env:AUDIT_CASE -in @('absent','absent_reference') -or ($env:AUDIT_CASE -ne 'timeout' -and $script:queries -gt 1)){return $null}
    $target=$state
    if($env:AUDIT_CASE -eq 'foreign'){$target+='Backup'}
    [pscustomobject]@{ExecutablePath=$env:AUDIT_PYTHON;CommandLine=('"C:\fixture\rig.py" --serve --state-dir "'+$target+'"');CreationDate=[datetime]'2026-10-25'}
}
function Start-Sleep {param($Milliseconds)}
function Start-Process {throw 'Real process launch is forbidden in the fixture'}
& (Join-Path $PSScriptRoot 'update.ps1') -Python $env:AUDIT_PYTHON
'''
                (root/'runner.ps1').write_text(runner,encoding='utf-8-sig')
                env={**os.environ,'LOCALAPPDATA':d,'AUDIT_CASE':scenario,'AUDIT_PYTHON':sys.executable}
                result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(root/'runner.ps1')],
                    env=env,capture_output=True,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                self.assertEqual(result.returncode==0,ok,result.stderr.decode(errors='replace'))
                self.assertEqual((root/'started.txt').exists(),ok)
                if ok:
                    started=json.loads((root/'started.txt').read_text())
                    self.assertEqual(started['reference'],scenario in ('reference','absent_reference'))
                    self.assertEqual(started['legacy_uuid'],'GPU-bafb0ad5-dd9e-ee1d-a0c0-221c0dfbfc75' if scenario in ('reference','absent_reference') else '')
                requests=list((state/'rig-requests').glob('*.json'))
                if scenario in ('foreign','stale'):self.assertEqual(requests,[])
                if scenario in ('graceful','timeout'):
                    self.assertEqual(json.loads(requests[0].read_text(encoding='utf-8-sig'))['action'],'shutdown')

    @windows_only("powershell.exe")
    def test_rust_motor_builds_aside_and_swaps_the_executable_only_after_shutdown(self):
        """The running service locks rust/target/release/hashpilot.exe; the build goes to a staging target."""
        source=(Path(__file__).parent/'mining/windows/Atualizar-Rig.ps1').read_text(encoding='utf-8')
        source=source.replace("$principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())","$principal=$null")
        source=source.replace("if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){","if($false){")
        for scenario,ok in [('rust',True),('rust_build_fails',False)]:
            with self.subTest(scenario=scenario),tempfile.TemporaryDirectory() as d:
                root=Path(d);state=root/'PearlMonitor';state.mkdir()
                (state/'rig-motor.txt').write_text('Rust',encoding='utf-8')
                (root/'rig.py').write_text('',encoding='utf-8')
                release=root/'rust'/'target'/'release';release.mkdir(parents=True)
                (release/'hashpilot.exe').write_text('velho',encoding='ascii')
                (root/'update.ps1').write_text(source,encoding='utf-8-sig')
                (root/'Iniciar-Rig.ps1').write_text("param([string]$Python,[switch]$ReferenciaRTX3070,[string]$LegacyUuid)\nSet-Content -LiteralPath (Join-Path $PSScriptRoot 'started.txt') -Value ((Get-Content -LiteralPath (Join-Path $PSScriptRoot 'rust\\target\\release\\hashpilot.exe') -Raw).Trim())",encoding='utf-8')
                runner=r'''
$ErrorActionPreference='Stop'
$state=Join-Path $env:LOCALAPPDATA 'PearlMonitor'
$now=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0
@{pid=42;marker='HashPilotRig-v1';sampled_at=$now}|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $state 'rig-manager.json')
$script:queries=0
$exe=Join-Path $PSScriptRoot 'rust\target\release\hashpilot.exe'
function Get-CimInstance {
    param($ClassName,$Filter,$ErrorAction)
    $script:queries++
    if($script:queries -gt 1){return $null}
    [pscustomobject]@{ExecutablePath=$exe;CommandLine=('"'+$exe+'" rig --serve --state-dir "'+$state+'"');CreationDate=[datetime]'2026-10-25'}
}
function mise {
    if($env:AUDIT_CASE -eq 'rust_build_fails'){$global:LASTEXITCODE=1;return}
    $target=$args[$args.IndexOf('--target-dir')+1]
    New-Item -ItemType Directory -Path (Join-Path $target 'release') -Force | Out-Null
    # The "running" executable is still the old one while the build runs.
    if((Get-Content -LiteralPath $exe -Raw).Trim() -ne 'velho'){throw 'executavel trocado antes da parada'}
    Set-Content -LiteralPath (Join-Path $target 'release\hashpilot.exe') -Value 'novo'
    $global:LASTEXITCODE=0
}
function Start-Sleep {param($Milliseconds)}
function Start-Process {throw 'Real process launch is forbidden in the fixture'}
& (Join-Path $PSScriptRoot 'update.ps1') -Python $env:AUDIT_PYTHON
'''
                (root/'runner.ps1').write_text(runner,encoding='utf-8-sig')
                env={**os.environ,'LOCALAPPDATA':d,'AUDIT_CASE':scenario,'AUDIT_PYTHON':sys.executable}
                result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(root/'runner.ps1')],
                    env=env,capture_output=True,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                self.assertEqual(result.returncode==0,ok,result.stderr.decode(errors='replace'))
                requests=list((state/'rig-requests').glob('*.json')) if (state/'rig-requests').exists() else []
                if ok:
                    self.assertEqual((root/'started.txt').read_text().strip(),'novo')
                    self.assertEqual(len(requests),1)
                else:
                    # Build failure: nothing stopped, nothing started, old executable kept.
                    self.assertFalse((root/'started.txt').exists())
                    self.assertEqual(requests,[])
                    self.assertEqual((release/'hashpilot.exe').read_text().strip(),'velho')
