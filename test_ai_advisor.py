import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from ai_advisor import configuration,telemetry_context,RequestBudget,RoutedAdvisor,request_json
from ai_setup import save_ai


class CloudAdviceTests(unittest.TestCase):
    def setup_advisor(self,directory,provider='openrouter',model='openrouter/free',consent=True):
        path=Path(directory)/'settings.json'
        save_ai(path,True,provider,model,consent)
        return RoutedAdvisor(directory,path)

    def test_only_free_cloud_ids_and_known_providers(self):
        for provider,model in [('openrouter','paid/model'),('opencode','opencode/gpt-6-sol'),('arbitrary','anything')]:
            with self.subTest(provider=provider),self.assertRaises(ValueError):
                configuration({'ai':{'provider':provider,'model':model}})

    def test_legacy_local_choice_preserved_new_installs_recommend_cloud(self):
        self.assertEqual(configuration({'ai_enabled':True})['provider'],'ollama')
        self.assertEqual(configuration({})['provider'],'openrouter')
        self.assertFalse(configuration({})['enabled'])

    def test_minimum_cloud_context_has_no_identity_paths_wallet_or_credential(self):
        context={'gpu_uuid':'private','wallet':'private','key':'private','sample':{'temperature_c':70,
            'uuid':'private','serial':'private','path':'private','power_w':float('nan')},
            'benchmark':{'mean_hashrate_ths':80,'device':{'serial':'private'},'log':'private'}}
        self.assertEqual(telemetry_context(context),{'sample':{'temperature_c':70},'benchmark':{'mean_hashrate_ths':80}})

    def test_key_is_kept_out_of_settings_and_preserves_other_settings(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'settings.json';path.write_text('{"gpu_settings":{"GPU-x":{"enabled":false}}}')
            with patch('ai_setup.credential') as vault:
                save_ai(path,True,'openrouter','openrouter/free',True,'dummy-test-value')
            vault.assert_called_once_with('openrouter','dummy-test-value')
            self.assertNotIn('dummy-test-value',path.read_text())
            self.assertFalse(json.loads(path.read_text())['gpu_settings']['GPU-x']['enabled'])

    def test_cloud_consent_cannot_be_implicitly_enabled(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):self.setup_advisor(d,consent=False)
            self.assertFalse((Path(d)/'settings.json').exists())

    def test_shared_quota_survives_restart_limits_failed_attempts_and_resets_utc(self):
        with tempfile.TemporaryDirectory() as d:
            t=1792886400
            RequestBudget(d).reserve(t)
            with self.assertRaises(ValueError):RequestBudget(d).reserve(t+20)
            for i in range(1,40):RequestBudget(d).reserve(t+i*61)
            with self.assertRaises(ValueError):RequestBudget(d).reserve(t+40*61)
            RequestBudget(d).reserve(t+86400)

    def test_catalog_price_change_blocks_inference_before_budget_or_charge(self):
        with tempfile.TemporaryDirectory() as d:
            advisor=self.setup_advisor(d)
            with patch('ai_advisor.credential',return_value='test'),patch('ai_advisor.request_json',return_value={
                'data':[{'id':'openrouter/free','pricing':{'prompt':'0','completion':'1'}}]}) as fetch:
                with self.assertRaises(ValueError):advisor.advise({})
            self.assertEqual(fetch.call_count,1)
            self.assertFalse((Path(d)/'ai-requests.sqlite3').exists())

    def test_free_call_has_zero_price_guard_no_paid_fallback_and_strict_response(self):
        with tempfile.TemporaryDirectory() as d:
            advisor=self.setup_advisor(d)
            with patch('ai_advisor.credential',return_value='dummy-test'),patch('ai_advisor.request_json',side_effect=[
                {'data':[{'id':'openrouter/free','pricing':{'prompt':'0','completion':'0'}}]},
                {'choices':[{'message':{'content':'{"action":"hold","reason":"Estável"}'}}]}]) as fetch:
                self.assertEqual(advisor.advise({'wallet':'never-send'})['action'],'hold')
            payload=fetch.call_args.args[1]
            self.assertEqual(payload['provider']['max_price'],{'prompt':0,'completion':0,'request':0})
            self.assertNotIn('models',payload)
            self.assertNotIn('never-send',json.dumps(payload))

    def test_cloud_suggestion_cannot_inject_commands_or_extra_settings(self):
        from gpu_agent import OllamaAdvisor
        for content in ['{"action":"bash","reason":"x"}','{"action":"hold","reason":"x","clock":2100}']:
            with self.assertRaises(ValueError):OllamaAdvisor.parse(content)

    def test_provider_error_does_not_echo_credential(self):
        from urllib.error import HTTPError
        with patch('ai_advisor.build_opener') as opener:
            opener.return_value.open.side_effect=HTTPError('https://example',401,'secret-value',{},None)
            with self.assertRaises(ValueError) as error:request_json('https://example',{},'secret-value')
        self.assertNotIn('secret-value',str(error.exception))

    def test_opencode_uses_free_client_no_shell_and_denies_all_tools(self):
        with tempfile.TemporaryDirectory() as d:
            advisor=self.setup_advisor(d,'opencode','opencode/test-free')
            def run(argv,**kwargs):
                self.assertEqual(argv[:3],['opencode.exe','--pure','run'])
                self.assertNotIn('shell',kwargs)
                conf=json.loads(kwargs['env']['OPENCODE_CONFIG_CONTENT'])
                self.assertEqual(conf['permission'],{'*':'deny'})
                self.assertEqual(conf['model'],conf['small_model'])
                self.assertEqual(conf['plugin'],[])
                kwargs['stdout'].write(json.dumps({'type':'text','part':{'text':'{"action":"hold","reason":"ok"}'}}).encode())
                return SimpleNamespace(returncode=0)
            with patch('ai_advisor.shutil.which',return_value='opencode.exe'),patch('ai_advisor.request_json',
                return_value={'data':[{'id':'test-free'}]}),patch('ai_advisor.subprocess.run',side_effect=run):
                self.assertEqual(advisor.advise({})['action'],'hold')

    def test_missing_opencode_does_not_fall_back_to_direct_paid_api(self):
        with tempfile.TemporaryDirectory() as d:
            advisor=self.setup_advisor(d,'opencode','opencode/test-free')
            with patch('ai_advisor.shutil.which',return_value=None),patch('ai_advisor.request_json') as fetch:
                with self.assertRaises(ValueError):advisor.advise({})
            fetch.assert_not_called()
