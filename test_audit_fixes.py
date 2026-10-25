"""Regression fixtures for the 2026-10-25 audit; never touch real state or GPUs."""
import json
import os
import queue
from pathlib import Path
import sqlite3
from contextlib import closing
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from gpu_agent import NvidiaActuator, process_state
from history import HistoryStore
from monitor import Monitor, selected_gpu_context


class LockRegression(unittest.TestCase):
    def test_invalid_pid_never_proves_death(self):
        for pid in (True,False,0,-1,2**40,'123',None):
            with self.subTest(pid=pid):self.assertEqual(process_state(pid),'unknown')

    def test_abrupt_exit_releases_os_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            script=("from gpu_agent import NvidiaActuator; import os; "
                    "a=NvidiaActuator(os.environ['AUDIT_DIR'],enabled=True,command=lambda _:None); "
                    "a.acquire('GPU-fixture'); os._exit(0)")
            result=subprocess.run([sys.executable,'-c',script],env={**os.environ,'AUDIT_DIR':temporary},
                                  cwd=Path(__file__).parent,timeout=10)
            self.assertEqual(result.returncode,0)
            new=NvidiaActuator(temporary,enabled=True,command=lambda _:None)
            new.acquire('GPU-fixture')
            new.close()

    def test_legacy_unknown_blocks_and_dead_recovers_without_unlink_window(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'gpu-control-owner.json'
            path.write_text(json.dumps({'pid':123,'token':'old','gpu_uuid':'GPU-fixture'}),encoding='utf-8')
            actuator=NvidiaActuator(temporary,enabled=True,command=lambda _:None)
            with patch('gpu_agent.process_state',return_value='unknown'):
                with self.assertRaises(RuntimeError):actuator.acquire('GPU-fixture')
            self.assertIsNone(actuator.handle)
            with patch('gpu_agent.process_state',return_value='alive'):
                with self.assertRaises(RuntimeError):actuator.acquire('GPU-fixture')
            with patch('gpu_agent.process_state',return_value='dead'):
                actuator.acquire('GPU-fixture')
            self.assertTrue(path.exists())
            with self.assertRaises(FileExistsError):path.open('x')
            second=NvidiaActuator(temporary,enabled=True,command=lambda _:None)
            with self.assertRaises(RuntimeError):second.acquire('GPU-fixture')
            path.write_text('{broken',encoding='utf-8')
            with self.assertRaises((ValueError,RuntimeError)):actuator.assert_owner('GPU-fixture')
            actuator.close()
            self.assertIsNone(actuator.handle)
            self.assertTrue(path.exists())


class HistoryRegression(unittest.TestCase):
    LEGACY='''CREATE TABLE gpu_telemetry (
        gpu_uuid TEXT NOT NULL,timestamp REAL NOT NULL,wallet TEXT NOT NULL,session TEXT NOT NULL,
        source TEXT NOT NULL,gpu_json TEXT,miner_json TEXT NOT NULL,miner_running INTEGER,
        enabled INTEGER,errors_json TEXT,log_path TEXT,PRIMARY KEY(gpu_uuid,timestamp))'''

    def legacy(self,connection):
        connection.execute(self.LEGACY)
        connection.execute('CREATE INDEX gpu_telemetry_wallet_time ON gpu_telemetry(wallet,timestamp)')
        connection.execute("INSERT INTO gpu_telemetry VALUES('GPU-X',123,'A','s','live','{}','{}',1,1,'{}','x')")
        connection.execute('PRAGMA user_version=2')
        connection.commit()

    def test_migration_preserves_rows_and_accepts_same_timestamp_other_wallet(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'history.sqlite3'
            with closing(sqlite3.connect(path)) as connection:
                self.legacy(connection)
                HistoryStore.schema(None,connection)
                HistoryStore.schema(None,connection)
                self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0],3)
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM gpu_telemetry').fetchone()[0],1)
            store=HistoryStore(path,'fallback')
            try:
                store.rig_telemetry({'devices':[{'uuid':'GPU-X','wallet':'B','collected_at':123,'gpu':{'temperature.gpu':70},'miner':{}}]})
                self.assertTrue(store.flush())
                self.assertEqual(store.restore_gpu('GPU-X',0,'A')['count'],1)
                self.assertEqual(store.restore_gpu('GPU-X',0,'B')['count'],1)
            finally:store.close()

    def test_migration_rollback_after_drop(self):
        class FailingConnection:
            def __init__(self,wrapped):self.wrapped=wrapped
            def execute(self,sql,*args):
                if sql.startswith('ALTER TABLE gpu_telemetry_new'):raise sqlite3.OperationalError('injected')
                return self.wrapped.execute(sql,*args)
            def commit(self):self.wrapped.commit()
            def rollback(self):self.wrapped.rollback()
        with closing(sqlite3.connect(':memory:')) as connection:
            self.legacy(connection)
            with self.assertRaises(sqlite3.OperationalError):HistoryStore.schema(None,FailingConnection(connection))
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0],2)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM gpu_telemetry').fetchone()[0],1)
            self.assertFalse(connection.execute("SELECT 1 FROM sqlite_master WHERE name='gpu_telemetry_new'").fetchone())
            self.assertTrue(connection.execute("SELECT 1 FROM sqlite_master WHERE name='gpu_telemetry_wallet_time'").fetchone())

    def test_future_schema_version_is_refused_unchanged(self):
        with closing(sqlite3.connect(':memory:')) as connection:
            self.legacy(connection)
            connection.execute('PRAGMA user_version=4')
            with self.assertRaises(RuntimeError):HistoryStore.schema(None,connection)
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0],4)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM gpu_telemetry').fetchone()[0],1)
            self.assertFalse(connection.execute("SELECT 1 FROM sqlite_master WHERE name='telemetry'").fetchone())

    def test_worker_reports_future_schema_and_closes_without_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'future.sqlite3'
            with closing(sqlite3.connect(path)) as connection:
                self.legacy(connection)
                connection.execute('PRAGMA user_version=4')
            store=HistoryStore(path,'wallet-A')
            self.assertTrue(store.ready.wait(2))
            self.assertTrue(store.flush())
            self.assertIn('Versão futura',store.last_error)
            self.assertFalse(store.telemetry({'collected_at':456,'gpu':{},'miner':{}}))
            store.close()
            self.assertFalse(store.worker.is_alive())
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0],4)
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM gpu_telemetry').fetchone()[0],1)
                self.assertFalse(connection.execute("SELECT 1 FROM sqlite_master WHERE name='telemetry'").fetchone())

    def test_chart_context_uses_device_wallet(self):
        first={'rig':{'devices':[{'uuid':'GPU-X','wallet':'A'}]}}
        second={'rig':{'devices':[{'uuid':'GPU-X','wallet':'B'}]}}
        self.assertEqual(selected_gpu_context(first,'GPU-X','global'),('GPU-X','A'))
        self.assertEqual(selected_gpu_context(second,'GPU-X','global'),('GPU-X','B'))
        self.assertIsNone(selected_gpu_context(second,'GPU-Y','global'))

    def test_delayed_restore_uses_current_wallet_and_waits_for_selection(self):
        class Chart:
            def __init__(self):self.points={}
            def add(self,stamp,value):self.points[stamp]=value
            def draw(self):pass
        with tempfile.TemporaryDirectory() as temporary:
            calls=[]
            fake=SimpleNamespace(
                last_ui_request=0,tray_actions=queue.Queue(),history_restore=queue.Queue(),
                collector=SimpleNamespace(samples=queue.Queue()),finance_collector=SimpleNamespace(samples=queue.Queue()),
                history=SimpleNamespace(wallet='global',restore_gpu=lambda uuid,since,wallet:
                    (calls.append((uuid,wallet)) or {'points':{'temp':[(2,70)]}})),
                charts={'temp':Chart()},restored_points={},last_data={'rig':{'devices':[{'uuid':'GPU-X','wallet':'B'}]}},
                selected_gpu_uuid=None,_chart_context=None,restore_window_preferences=lambda _:None,
                tabs=SimpleNamespace(select=lambda *_:None),after=lambda *_:None,poll=lambda:None,
                refresh_finance=lambda:None,refresh_summary=lambda:None)
            fake.history_restore.put({'points':{'temp':[(1,60)]},'preferences':{}})
            with patch('monitor.STATE_DIR',Path(temporary)):Monitor.poll(fake)
            self.assertEqual(calls,[])
            self.assertEqual(fake.charts['temp'].points,{})
            fake.selected_gpu_uuid='GPU-X';fake._chart_context=('GPU-X','B')
            fake.history_restore.put({'points':{'temp':[(1,60)]},'preferences':{}})
            with patch('monitor.STATE_DIR',Path(temporary)):Monitor.poll(fake)
            self.assertEqual(calls,[('GPU-X','B')])
            self.assertEqual(fake.charts['temp'].points,{2:70})


class InstallerRegression(unittest.TestCase):
    def run_install_block(self,root,fail_move=False):
        script=r'''
$ErrorActionPreference='Stop'
$sourceText=Get-Content -LiteralPath (Join-Path $env:AUDIT_REPO 'install.ps1') -Raw
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseInput($sourceText,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Installer parse failed'}
$functions=$ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst]},$true)
foreach($name in @('Get-ApplicationFiles','Test-ApplicationCopy')){
    $definition=$functions | Where-Object { $_.Name -eq $name } | Select-Object -First 1
    if(-not $definition){throw ('Missing function '+$name)}
    Invoke-Expression $definition.Extent.Text
}
$source=Join-Path $env:AUDIT_ROOT 'source'
$InstallRoot=Join-Path $env:AUDIT_ROOT 'install'
$revision='revision-fixture'
$SourcePath=$source
$python='fixture-python'
if($env:AUDIT_FAIL_MOVE -eq '1'){function Move-Item {throw 'injected move failure'}}
$start=$sourceText.IndexOf("Write-Host '[3/5]")
$end=$sourceText.IndexOf("Write-Host '[4/5]")
if($start -lt 0 -or $end -le $start){throw 'Installer block missing'}
Invoke-Expression $sourceText.Substring($start,$end-$start)
'''
        runner=root/'runner.ps1'
        runner.write_text(script,encoding='utf-8-sig')
        env={**os.environ,'AUDIT_ROOT':str(root),'AUDIT_REPO':str(Path(__file__).parent),
             'AUDIT_FAIL_MOVE':'1' if fail_move else '0'}
        literal="'"+str(runner).replace("'","''")+"'"
        command='& ([scriptblock]::Create([IO.File]::ReadAllText('+literal+')))'
        return subprocess.run(['powershell.exe','-NoProfile','-Command',command],
                              env=env,capture_output=True,timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))

    def test_partial_version_is_preserved_and_new_version_activated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'source';source.mkdir()
            (source/'monitor.py').write_text('complete',encoding='utf-8')
            (source/'.env').write_text('fixture secret',encoding='utf-8')
            (source/'settings.json').write_text('fixture settings',encoding='utf-8')
            installed=root/'install';old=installed/'versions'/'revision-fixture';old.mkdir(parents=True)
            (old/'monitor.py').write_text('partial',encoding='utf-8')
            (installed/'installation.json').write_text('{"app":"previous"}',encoding='utf-8')
            result=self.run_install_block(root)
            self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
            metadata=json.loads((installed/'installation.json').read_text(encoding='utf-8-sig'))
            new=Path(metadata['app'])
            self.assertNotEqual(new,old)
            self.assertTrue(new.name.startswith('revision-fixture-repair-'))
            self.assertEqual((old/'monitor.py').read_text(encoding='utf-8'),'partial')
            self.assertEqual((new/'monitor.py').read_text(encoding='utf-8'),'complete')
            self.assertFalse((new/'.env').exists())
            self.assertFalse((new/'settings.json').exists())

    def test_promotion_failure_preserves_previous_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'source';source.mkdir()
            (source/'monitor.py').write_text('complete',encoding='utf-8')
            installed=root/'install';old=installed/'versions'/'revision-fixture';old.mkdir(parents=True)
            (old/'monitor.py').write_text('partial',encoding='utf-8')
            (installed/'installation.json').write_text('{"app":"previous"}',encoding='utf-8')
            result=self.run_install_block(root,fail_move=True)
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(json.loads((installed/'installation.json').read_text(encoding='utf-8'))['app'],'previous')
            self.assertEqual((old/'monitor.py').read_text(encoding='utf-8'),'partial')

    def test_complete_version_is_reused_but_same_size_hash_mismatch_is_repaired(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'source';source.mkdir()
            (source/'monitor.py').write_text('good',encoding='utf-8')
            old=root/'install'/'versions'/'revision-fixture';old.mkdir(parents=True)
            (old/'monitor.py').write_text('good',encoding='utf-8')
            first=self.run_install_block(root)
            self.assertEqual(first.returncode,0,first.stderr.decode(errors='replace'))
            metadata=root/'install'/'installation.json'
            self.assertEqual(Path(json.loads(metadata.read_text(encoding='utf-8-sig'))['app']),old)
            (old/'monitor.py').write_text('evil',encoding='utf-8')
            second=self.run_install_block(root)
            self.assertEqual(second.returncode,0,second.stderr.decode(errors='replace'))
            repaired=Path(json.loads(metadata.read_text(encoding='utf-8-sig'))['app'])
            self.assertNotEqual(repaired,old)
            self.assertEqual((old/'monitor.py').read_text(encoding='utf-8'),'evil')
            self.assertEqual((repaired/'monitor.py').read_text(encoding='utf-8'),'good')

    def test_unexpected_python_file_forces_new_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'source';source.mkdir()
            (source/'monitor.py').write_text('good',encoding='utf-8')
            old=root/'install'/'versions'/'revision-fixture';old.mkdir(parents=True)
            (old/'monitor.py').write_text('good',encoding='utf-8')
            (old/'injected.py').write_text('fixture only',encoding='utf-8')
            result=self.run_install_block(root)
            self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
            repaired=Path(json.loads((root/'install'/'installation.json').read_text(encoding='utf-8-sig'))['app'])
            self.assertNotEqual(repaired,old)
            self.assertTrue((old/'injected.py').exists())
            self.assertFalse((repaired/'injected.py').exists())


if __name__=='__main__':unittest.main()
