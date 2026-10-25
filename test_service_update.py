"""Exercise service handoff with fake processes in a temporary AppData only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

class ServiceUpdateTests(unittest.TestCase):
    def test_guarded_service_handoff_never_forces_a_process_or_changes_real_appdata(self):
        source=(Path(__file__).parent/'mining/windows/Atualizar-Rig.ps1').read_text(encoding='utf-8')
        # Mock the privilege context only in this isolated fixture. Production
        # still requires real Windows UAC before any shutdown request is written.
        source=source.replace("$principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())","$principal=$null")
        source=source.replace("if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){","if($false){")
        for scenario,ok in [('graceful',True),('absent',True),('foreign',False),('stale',False),('timeout',False)]:
            with self.subTest(scenario=scenario),tempfile.TemporaryDirectory() as d:
                root=Path(d);state=root/'PearlMonitor';state.mkdir()
                (root/'update.ps1').write_text(source,encoding='utf-8-sig')
                (root/'Iniciar-Rig.ps1').write_text("param([string]$Python)\nSet-Content -LiteralPath (Join-Path $PSScriptRoot 'started.txt') -Value 'fixture only'",encoding='utf-8')
                runner=r'''
$ErrorActionPreference='Stop'
$state=Join-Path $env:LOCALAPPDATA 'PearlMonitor'
$now=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0
if($env:AUDIT_CASE -eq 'stale'){$now-=3600}
@{pid=42;marker='HashPilotRig-v1';sampled_at=$now}|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $state 'rig-manager.json')
$script:queries=0
function Get-CimInstance {
    param($ClassName,$Filter,$ErrorAction)
    $script:queries++
    if($env:AUDIT_CASE -eq 'absent' -or ($env:AUDIT_CASE -ne 'timeout' -and $script:queries -gt 1)){return $null}
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
                requests=list((state/'rig-requests').glob('*.json'))
                if scenario in ('foreign','stale'):self.assertEqual(requests,[])
                if scenario in ('graceful','timeout'):
                    self.assertEqual(json.loads(requests[0].read_text(encoding='utf-8-sig'))['action'],'shutdown')
