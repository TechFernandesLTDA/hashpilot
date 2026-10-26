"""Updater isolado: sem rede, instalação real, mineração ou dados do usuário."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock,patch

import app_update as update


def release(tag,*,prerelease=False,draft=False):
    base=f'https://github.com/{update.REPOSITORY}/releases/download/{tag}/'
    return {'tag_name':tag,'prerelease':prerelease,'draft':draft,
            'assets':[{'name':name,'state':'uploaded','browser_download_url':base+name}
                      for name in (f'hashpilot-{tag}.zip','SHA256SUMS')]}


def installed(root,version='v1.2.3'):
    root.mkdir(parents=True,exist_ok=True)
    content=b'# fixture, never executed\n'
    (root/'monitor.py').write_bytes(content)
    (root/'RELEASE-MANIFEST.json').write_text(json.dumps({'schema':1,'version':version,'commit':'a'*40,
        'files':{'monitor.py':{'sha256':hashlib.sha256(content).hexdigest(),'size':len(content)}}}),encoding='utf-8')
    return root


class AppUpdateTests(unittest.TestCase):
    def test_semantic_order_ignores_older_and_same_release(self):
        chosen=update.select_release([release('v1.2.9'),release('v1.2.10'),release('v1.2.3')],'v1.2.3')
        self.assertEqual(chosen['version'],'v1.2.10')
        self.assertIsNone(update.select_release([release('v1.2.3')],'v1.2.3'))

    def test_prereleases_require_explicit_channel_and_drafts_never_offer(self):
        payload=[release('v2.0.0',prerelease=True),release('v3.0.0',draft=True)]
        self.assertIsNone(update.select_release(payload,'v1.0.0'))
        self.assertEqual(update.select_release(payload,'v1.0.0',True)['version'],'v2.0.0')

    def test_untrusted_missing_duplicate_or_malformed_assets_are_rejected(self):
        bad=release('v9.0.0');bad['assets'][0]['browser_download_url']='https://untrusted.example/installer.exe'
        missing=release('v8.0.0');missing['assets'].pop()
        duplicate=release('v7.0.0');duplicate['assets'].append(duplicate['assets'][0])
        invalid=release('v1.0.0');invalid['tag_name']='v1.0.0; powershell'
        self.assertIsNone(update.select_release([bad,missing,duplicate,invalid,None],'v1.0.0',True))
        with self.assertRaises(ValueError):update.select_release({},'v1.0.0')

    def test_manifest_is_authority_and_invalid_manifest_never_falls_back(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=installed(Path(temporary),'v4.0.0')
            self.assertEqual(update.current_version(root)[0],'v4.0.0')
            for invalid in ('null','[]','{}','{"schema":1,"commit":"wrong","version":"v4.0.0"}'):
                (root/'RELEASE-MANIFEST.json').write_text(invalid)
                with self.subTest(invalid=invalid),self.assertRaises(ValueError):update.current_version(root)

    def test_source_version_only_without_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(update.current_version(Path(temporary)),(update.VERSION,'código-fonte / desenvolvimento'))

    def test_visible_identity_uses_running_folder_not_activation_pointer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);app=installed(root/'old-running','v1.0.0')
            (root/'installation.json').write_text(json.dumps({'release':'v9.0.0','app':'elsewhere'}))
            identity=update.build_identity(app)
            self.assertEqual(identity['version'],'v1.0.0')
            self.assertEqual(identity['revision'],'a'*40)

    def test_invalid_visible_identity_never_claims_a_verified_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=installed(Path(temporary));(root/'RELEASE-MANIFEST.json').write_text('null')
            self.assertEqual(update.build_identity(root)['version'],'não verificada')
            self.assertIsNone(update.build_identity(root)['revision'])

    def test_preferences_are_separate_and_boolean_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            state=Path(temporary);settings=state/'settings.json';settings.write_bytes(b'keep all configured data')
            self.assertEqual(update.read_preferences(state)['include_prereleases'],update.RELEASE_CHANNEL=='prerelease')
            update.save_preferences(True,state);self.assertTrue(update.read_preferences(state)['include_prereleases'])
            update.save_preferences(False,state);self.assertFalse(update.read_preferences(state)['include_prereleases'])
            (state/'update-preferences.json').write_text('{"include_prereleases":"true"}')
            self.assertFalse(update.read_preferences(state)['include_prereleases'])
            self.assertEqual(settings.read_bytes(),b'keep all configured data')

    def test_public_request_is_bounded_and_has_no_credentials(self):
        opener=Mock(return_value=io.BytesIO(json.dumps([release('v2.0.0')]).encode()))
        self.assertEqual(update.check_release('v1.0.0',opener=opener)['version'],'v2.0.0')
        request=opener.call_args.args[0]
        self.assertEqual(request.full_url,update.API_URL)
        self.assertNotIn('Authorization',request.headers)
        with self.assertRaises(ValueError):update.check_release('v1.0.0',opener=lambda *a,**k:io.BytesIO(b'x'*(2*1024*1024+1)))

    def test_checker_error_releases_busy_and_includes_captured_channel(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(update,'current_version',side_effect=ValueError('bad manifest')):
            checker=update.UpdateChecker(Path(temporary));self.assertTrue(checker.start())
            result=checker.results.get(timeout=2)
            self.assertFalse(checker.busy);self.assertEqual(result['error'],'bad manifest')
            self.assertEqual(result['include_prereleases'],update.RELEASE_CHANNEL=='prerelease');self.assertFalse(checker.start())

    def test_custom_install_root_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'custom-install';app=installed(root/'versions'/'a')
            self.assertEqual(update.installation_root(app),root)

    def test_install_keeps_config_database_wal_and_logs_untouched(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);state=root/'state';state.mkdir();installation=root/'installation'
            app=installed(installation/'versions'/('a'*40))
            (installation/'installation.json').write_text(json.dumps({'app':str(app),'release':'v1.2.3'}))
            old=root/'old';old.mkdir();(old/'install.ps1').write_text('# trusted local fixture')
            originals={name:('preserve '+name).encode() for name in ('settings.json','history.sqlite3','history.sqlite3-wal','gpu-agent.sqlite3','miner.log')}
            for name,content in originals.items():(state/name).write_bytes(content)
            runner=Mock(return_value=subprocess.CompletedProcess([],0))
            with patch.object(update,'ROOT',old),patch.object(update,'state_directory',return_value=state):
                result=update.install_release('v1.2.3',installation,runner)
            self.assertEqual(result,app)
            arguments=runner.call_args.args[0]
            self.assertIn('-NoLaunch',arguments);self.assertNotIn('--setup',arguments);self.assertNotIn('RunAs',arguments)
            for name,content in originals.items():self.assertEqual((state/name).read_bytes(),content)

    def test_bad_version_does_not_invoke_installer(self):
        runner=Mock()
        with self.assertRaises(ValueError):update.install_release('v1.0.0; malicious',runner=runner)
        runner.assert_not_called()

    def test_malformed_activation_metadata_fails_with_recoverable_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);state=root/'state';state.mkdir();installation=root/'installation';installation.mkdir()
            old=root/'old';old.mkdir();(old/'install.ps1').write_text('# fixture only')
            runner=Mock(return_value=subprocess.CompletedProcess([],0))
            for payload in (None,[],{}, {'app':None},{'app':12},{'app':''}):
                with self.subTest(payload=payload):
                    (installation/'installation.json').write_text(json.dumps(payload))
                    with patch.object(update,'ROOT',old),patch.object(update,'state_directory',return_value=state):
                        with self.assertRaises(ValueError):update.install_release('v1.2.3',installation,runner)

    def test_install_lock_prevents_overlapping_installs(self):
        with tempfile.TemporaryDirectory() as temporary:
            state=Path(temporary)
            with update.installation_lock(state):
                with self.assertRaises(RuntimeError):
                    with update.installation_lock(state):self.fail('second lock acquired')
            with update.installation_lock(state):pass

    def test_corrupted_new_monitor_cannot_be_reopened(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(update.subprocess,'Popen') as start:
            app=installed(Path(temporary));(app/'monitor.py').write_text('changed')
            with self.assertRaises(ValueError):update.reopen_after_close(Mock(),app)
            start.assert_not_called()

    def test_reopen_helper_is_started_before_graceful_close_and_never_runs_setup(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(update.subprocess,'Popen') as start:
            app=installed(Path(temporary));parent=Mock();events=[]
            start.side_effect=lambda *a,**k:events.append('helper')
            parent.close.side_effect=lambda:events.append('close')
            update.reopen_after_close(parent,app)
            self.assertEqual(events,['helper','close']);self.assertFalse(parent.restart_requested)
            self.assertNotIn('--setup',start.call_args.args[0])


def gui_report():
    import tkinter as tk
    from test_responsive_ui import inspect_widgets
    root=tk.Tk();root.geometry('1x1+0+0');root.update();reports=[]
    def inspect():
        window=next(w for w in root.winfo_children() if isinstance(w,tk.Toplevel));window.update_idletasks()
        assert window.winfo_width()>600
        reports.append({'client':[window.winfo_width(),window.winfo_height()],'errors':inspect_widgets(window)})
        window.destroy()
    with patch.object(update.UpdateChecker,'start',return_value=False):
        for width,height in [(1280,720),(1920,1080),(3840,2160)]:
            with patch.object(tk.Misc,'winfo_screenwidth',return_value=width),patch.object(tk.Misc,'winfo_screenheight',return_value=height):
                root.after(250,inspect);update.show_update_setup(root)
    root.destroy();return reports


if __name__=='__main__':
    if '--gui' in __import__('sys').argv:
        reports=gui_report();print(json.dumps(reports));raise SystemExit(any(item['errors'] for item in reports))
    unittest.main()
