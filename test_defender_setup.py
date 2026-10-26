"""Exceções testadas com cmdlets simulados; nunca altera o Defender real."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import defender_setup


class DefenderPermissionTests(unittest.TestCase):
    def test_invalid_action_cannot_launch_elevation(self):
        with patch.object(defender_setup.subprocess, 'Popen') as launch:
            with self.assertRaises(ValueError):defender_setup.launch_exception_request('DisableAntivirus')
            launch.assert_not_called()

    def test_requests_are_explicit_and_use_fixed_script(self):
        with patch.object(defender_setup.subprocess, 'Popen') as launch:
            defender_setup.launch_exception_request('Remove')
            arguments=launch.call_args.args[0]
            self.assertEqual(arguments[-2:],['-Action','Remove'])
            self.assertEqual(Path(arguments[5]).name,'Permissao-Defender.ps1')

    def test_real_script_functions_fail_closed_and_only_touch_one_path(self):
        source=Path(__file__).parent/'mining/windows/Permissao-Defender.ps1'
        runner=r'''
$ErrorActionPreference='Stop'
# Load only functions through AST; no main block, dialogs or elevation.
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($env:AUDIT_SCRIPT,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Parse errors'}
$ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$false)|ForEach-Object {Invoke-Expression $_.Extent.Text}
$UserLocalAppData=$env:AUDIT_APPDATA
$script:added=@();$script:removed=@();$script:exclusions=@('C:\existing-unrelated.txt')
function Add-MpPreference {param($ExclusionPath,$ErrorAction)
 $script:added+=@($ExclusionPath)
 if($env:AUDIT_CASE -ne 'policy_denied'){$script:exclusions+=@($ExclusionPath)}
}
function Remove-MpPreference {param($ExclusionPath,$ErrorAction)
 $script:removed+=@($ExclusionPath)
 if($env:AUDIT_CASE -ne 'remove_denied'){$script:exclusions=@($script:exclusions|Where-Object {$_ -ne $ExclusionPath})}
}
function Get-MpPreference {[pscustomobject]@{ExclusionPath=$script:exclusions}}
function Get-FileHash {param($LiteralPath,$Algorithm)
 if($env:AUDIT_CASE -eq 'read_denied'){throw 'Fixture: acesso negado'}
 $hash='0'*64
 if($env:AUDIT_CASE -in @('valid_exe','policy_denied')){$hash='6eb4e0bc37d65fdf7644ab620737e14f4c7dfa9666acde43d5f38ab1e9094ac1'}
 if($env:AUDIT_CASE -in @('valid_archive','directory_target')){$hash='33eef5f8946259290013ce370a746a85f91c2bbcd81fcefa1aad904dd9a912f8'}
 [pscustomobject]@{Hash=$hash}
}
$base=Join-Path $UserLocalAppData 'Mining\Pearl'
New-Item -ItemType Directory -Path $base -Force|Out-Null
$directory=Join-Path $base 'SRBMiner-Multi-3-8-0'
New-Item -ItemType Directory -Path $directory -Force|Out-Null
$target=Join-Path $directory 'SRBMiner-MULTI.exe'
if($env:AUDIT_CASE -in @('valid_exe','invalid_exe','read_denied','policy_denied')){Set-Content -LiteralPath $target -Value 'fixture, never executable'}
if($env:AUDIT_CASE -in @('valid_archive','invalid_archive','directory_target')){Set-Content -LiteralPath (Join-Path $base 'SRBMiner-Multi-3-8-0-win64.zip') -Value 'fixture'}
if($env:AUDIT_CASE -eq 'directory_target'){New-Item -ItemType Directory -Path $target|Out-Null}
$operation='Add'
if($env:AUDIT_CASE -in @('remove_absent','remove_denied')){$operation='Remove';$script:exclusions+=@($target)}
if($env:AUDIT_CASE -eq 'junction'){
 $redirect=Join-Path $UserLocalAppData 'redirect'
 New-Item -ItemType Directory -Path $redirect|Out-Null
 $link=Join-Path $base 'junction'
 New-Item -ItemType Junction -Path $link -Target $redirect|Out-Null
 try{Assert-NoReparsePoint (Join-Path $link 'SRBMiner-MULTI.exe');$ok=$true}catch{$ok=$false}
}else{try{Set-MinerException $operation;$ok=$true}catch{$ok=$false}}
@{ok=$ok;added=$script:added;removed=$script:removed;unrelated_preserved=($script:exclusions -contains 'C:\existing-unrelated.txt');target=$target}|ConvertTo-Json -Compress
'''
        cases={'valid_exe':True,'valid_archive':True,'invalid_exe':False,'invalid_archive':False,
               'missing':False,'read_denied':False,'policy_denied':False,'remove_absent':True,
               'remove_denied':False,'junction':False,'directory_target':False}
        for case,expected in cases.items():
            with self.subTest(case=case),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);script=root/'fixture.ps1'
                script.write_text(runner,encoding='utf-8-sig')
                env={**os.environ,'AUDIT_SCRIPT':str(source),'AUDIT_APPDATA':str(root),'AUDIT_CASE':case}
                result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script)],
                    env=env,capture_output=True,text=True,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                self.assertEqual(result.returncode,0,result.stderr)
                report=json.loads(result.stdout)
                self.assertEqual(report['ok'],expected)
                self.assertTrue(report['unrelated_preserved'])
                self.assertEqual(report['added'],[report['target']] if case in ('valid_exe','valid_archive','policy_denied') else [])
                self.assertEqual(report['removed'],[report['target']] if case.startswith('remove_') else [])
