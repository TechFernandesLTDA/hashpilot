"""Reproductions from the October audit; no live GPU, paid API or real credentials."""
from collections import deque
from contextlib import closing
import copy
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ai_advisor import configuration,telemetry_context,RoutedAdvisor,RequestBudget,request_json
from ai_setup import prepare_ai
from contribution import AUTHOR_WALLET
from finance import financial_model,normalize_finance_state,FinanceCollector
from gpu_agent import BackgroundAdvisor,OllamaAdvisor,GPUAgent,numeric,read_sample
from gpu_telemetry import parse_gpu_log,read_log_tail
from history import HistoryStore
from monitor import Chart,fmt,safe_number,console_text
from payout import payout_model,duration,observed_rate,number
from rig import RigManager
from rig_config import resolve_devices,update_gpu_settings
from rig_panel import fresh_hash,totals
from valuation import value_in_currencies
from test_rig import FakeBackend,detected

NOW=dt.datetime(2026,10,25,12,0).timestamp()
def log(rate=80,stamp=NOW,tail='A:10 R:0 HW:0'):
    date=dt.datetime.fromtimestamp(stamp).strftime('%Y-%m-%d %H:%M:%S')
    return f'[{date}] GPU0 RTX: {rate} TH/s [T:70C P:150W {tail}]'
def state():
    return {'wallet':AUTHOR_WALLET,'sources':{
        'price':{'fetched_at':NOW,'data':{'pearl-2':{'brl':10,'usd':2}}},
        'network':{'fetched_at':NOW,'data':{'net_hashrate':100e12,'block_time':60,'block_reward':2,'fee':.03}},
        'balance':{'fetched_at':NOW,'data':{'total':2,'confirmed':1,'unconfirmed':1,'threshold':10}},
        'payouts':{'fetched_at':NOW,'data':{'paid':8}}},
        'snapshots':[{'timestamp':NOW-100,'total_prl':9},{'timestamp':NOW,'total_prl':10}]}
def sensor(now=NOW):return {'temperature_c':70,'power_w':150,'power_limit_w':240,'clock_mhz':1710,'sampled_at':now}


class MajorAuditTests(unittest.TestCase):
    def test_B01_expired_cloud_reply_reports_waiting_not_perpetual_pending(self):
        with tempfile.TemporaryDirectory() as d:
            source=RoutedAdvisor(d,config_override={'ai_enabled':True,'ai':{'provider':'openrouter'}})
            worker=BackgroundAdvisor(source)
            worker.config_signature=json.dumps(source.settings()['ai'],sort_keys=True)+'True'
            worker.advice={'action':'hold','reason':'ok'};worker.completed_at=100;worker.last_started=90
            result=worker.poll({},now=200)
            self.assertIsNone(result['advice']);self.assertFalse(result['in_flight'])
            self.assertIn('em espera',result['status'])

    def test_B02_provider_change_invalidates_old_inflight_result(self):
        with tempfile.TemporaryDirectory() as d:
            source=RoutedAdvisor(d,config_override={'ai_enabled':True,'ai':{'provider':'ollama'}})
            worker=BackgroundAdvisor(source)
            class Alive:
                def is_alive(self):return True
            worker.thread=Alive();worker.poll({},now=10);old=worker.revision
            source.config_override={'ai_enabled':True,'ai':{'provider':'openrouter'}}
            worker.poll({},now=11)
            with patch.object(source,'advise',return_value={'action':'clock_up','reason':'old'}):worker._work({},old)
            self.assertIsNone(worker.advice)

    def test_B03_invalid_ai_configuration_does_not_enable_or_crash_control_gate(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'settings.json';path.write_text('invalid json')
            source=RoutedAdvisor(d)
            self.assertFalse(source.enabled())
            self.assertIn('unavailable',BackgroundAdvisor(source).poll({})['status'])

    def test_B04_testing_draft_settings_does_not_save_settings_or_secret(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'settings.json';original='{"ai_enabled":false}'
            path.write_text(original)
            draft=prepare_ai(json.loads(original),True,'openrouter','openrouter/free',True)
            with patch('ai_advisor.credential') as vault,patch('ai_advisor.request_json',side_effect=ValueError('offline')):
                with self.assertRaises(ValueError):RoutedAdvisor(d,config_override=draft,key_override='dummy').advise({})
            vault.assert_not_called();self.assertEqual(path.read_text(),original)

    def test_B05_failed_miner_stop_preserves_ownership_and_cannot_duplicate_worker(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);settings=root/'settings.json';settings.write_text(json.dumps({'wallet':AUTHOR_WALLET}))
            backend=FakeBackend();manager=RigManager(root/'state',root/'logs',settings,root/'SRBMiner-MULTI.exe',root/'python.exe',
                backend=backend,detector=lambda:detected(1),clock=lambda:1000)
            manager.reconcile();before=len(backend.started);entry=manager.devices['GPU-test-0'];pid=entry['miner_pid']
            with patch.object(backend,'stop',return_value=False):
                self.assertFalse(manager.stop_gpu('GPU-test-0'));manager.reconcile()
            self.assertEqual(manager.devices['GPU-test-0']['miner_pid'],pid)
            self.assertEqual(len(backend.started),before)

    def test_B06_future_log_measurement_cannot_freeze_live_hashrate(self):
        parsed=parse_gpu_log(log(999,NOW+3600)+'\n'+log(80,NOW),'GPU-x',0,now=NOW)
        self.assertEqual(parsed['hashrate'],80);self.assertEqual(parsed['hashrate_at'],NOW)

    def test_B07_large_utf16_log_tail_preserves_text_after_bom_is_outside_tail(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'mining.log';path.write_text('header\n'*100+log(),encoding='utf-16')
            self.assertIn('80 TH/s',read_log_tail(path,limit=300))

    def test_B08_malformed_history_job_cannot_kill_or_block_later_samples(self):
        with tempfile.TemporaryDirectory() as d:
            store=HistoryStore(Path(d)/'history.sqlite3',AUTHOR_WALLET)
            try:
                store.submit('event',{'timestamp':1})
                store.event(2,'valid_after_bad',{})
                self.assertTrue(store.flush())
                self.assertTrue(store.worker.is_alive())
                self.assertEqual(store.read("select count(*) from events where kind='valid_after_bad'")[0][0],1)
            finally:store.close()

    def test_B09_stale_network_cannot_generate_current_income_forecast(self):
        s=state();s['sources']['network']['fetched_at']=NOW-3600
        self.assertIsNone(financial_model(s,80,NOW)['forecast'])

    def test_B10_out_of_order_pool_snapshots_do_not_reverse_income(self):
        s=state();s['snapshots'].reverse()
        self.assertEqual(financial_model(s,80,NOW)['periods']['day']['prl'],1)

    def test_B11_hardware_errors_prevent_a_validated_benchmark(self):
        with tempfile.TemporaryDirectory() as d:
            agent=GPUAgent(d);gpu=detected(1)[0]
            try:
                agent.start_benchmark(gpu,300,now=NOW)
                for elapsed in range(0,301,10):
                    agent.observe(gpu,sensor(NOW+elapsed),80,10,0,now=NOW+elapsed,hash_sampled_at=NOW+(elapsed//60)*60,hw_errors=1)
                self.assertFalse(agent.status(gpu['uuid'],now=NOW+300)['validated'])
            finally:agent.close()


class MicroAuditTests(unittest.TestCase):
    def test_M01_invalid_settings_root_has_controlled_validation_error(self):
        with self.assertRaises(ValueError):configuration([])
    def test_M02_invalid_provider_type_has_controlled_validation_error(self):
        with self.assertRaises(ValueError):configuration({'ai':{'provider':[]}})
    def test_M03_false_model_is_not_silently_replaced_with_another_model(self):
        with self.assertRaises(ValueError):configuration({'ai':{'model':False}})
    def test_M04_string_false_cannot_enable_cloud_consent(self):
        with self.assertRaises(ValueError):prepare_ai({},True,'openrouter','openrouter/free','false')
    def test_M05_string_false_cannot_enable_ai(self):
        with self.assertRaises(ValueError):prepare_ai({},'false','openrouter','openrouter/free',True)
    def test_M06_nonobject_context_has_controlled_validation_error(self):
        with self.assertRaises(ValueError):telemetry_context([])
    def test_M07_boolean_sensor_is_not_a_numeric_reading(self):
        self.assertEqual(telemetry_context({'sample':{'temperature_c':True}})['sample'],{})
    def test_M08_oversized_integer_sensor_does_not_crash_sanitizer(self):
        self.assertEqual(telemetry_context({'sample':{'temperature_c':10**500}})['sample'],{})
    def test_M09_negative_power_is_not_sent_as_valid_cloud_telemetry(self):
        self.assertEqual(telemetry_context({'sample':{'power_w':-1}})['sample'],{})
    def test_M10_global_cloud_interval_survives_utc_midnight(self):
        with tempfile.TemporaryDirectory() as d:
            t=dt.datetime(2026,10,25,23,59,50,tzinfo=dt.timezone.utc).timestamp()
            RequestBudget(d).reserve(t)
            with self.assertRaises(ValueError):RequestBudget(d).reserve(t+20)
    def test_M11_invalid_quota_timestamp_does_not_enter_sqlite(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):RequestBudget(d).reserve(True)
            self.assertFalse((Path(d)/'ai-requests.sqlite3').exists())
    def test_M12_malformed_provider_json_has_clear_safe_error(self):
        response=SimpleNamespace(read=lambda limit:b'not JSON',__enter__=None)
        class Reply:
            def __enter__(self):return response
            def __exit__(self,*args):pass
        with patch('ai_advisor.build_opener') as opener:
            opener.return_value.open.return_value=Reply()
            with self.assertRaisesRegex(ValueError,'JSON'):request_json('https://example')
    def test_M13_multiline_key_is_rejected_before_http_headers(self):
        with patch('ai_advisor.build_opener') as opener:
            with self.assertRaises(ValueError):request_json('https://example',{},'abc\nInjected: value')
            opener.assert_not_called()
    def test_M14_empty_reason_cannot_count_as_valid_advice(self):
        with self.assertRaises(ValueError):OllamaAdvisor.parse('{"action":"hold","reason":"   "}')
    def test_M15_nontext_model_reply_has_controlled_validation_error(self):
        with self.assertRaises(ValueError):OllamaAdvisor.parse({})
    def test_M16_local_ollama_cannot_select_cloud_suffix_implicitly(self):
        with self.assertRaises(ValueError):OllamaAdvisor('gemma4:cloud')
    def test_M17_nonobject_sample_section_is_omitted(self):
        self.assertEqual(telemetry_context({'sample':['private']})['sample'],{})
    def test_M18_oversized_numeric_conversion_is_unknown(self):
        self.assertIsNone(numeric(10**500));self.assertIsNone(number(10**500))
    def test_M19_overflowed_currency_equivalent_is_unknown(self):
        self.assertIsNone(value_in_currencies(1e308,1e308)['brl'])
    def test_M20_invalid_payout_duration_is_unknown(self):
        self.assertEqual(duration(float('nan')),'não disponível')
    def test_M21_future_balance_timestamp_is_stale(self):
        s=state();s['sources']['balance']['fetched_at']=NOW+100
        self.assertTrue(payout_model(s,now=NOW)['stale'])
    def test_M22_future_payment_is_not_reported_as_already_sent(self):
        s=state();s['sources']['payout_history']={'data':{'results':[{'date':NOW+3600,'received':1}]}}
        self.assertIsNone(payout_model(s,now=NOW)['last_payment'])
    def test_M23_future_bitcoin_price_is_marked_stale(self):
        s=state();s['sources']['price']['data']['bitcoin']={'usd':100000,'last_updated_at':NOW+1000}
        self.assertTrue(financial_model(s,80,NOW)['btc_price_stale'])
    def test_M24_negative_hashrate_cannot_forecast_negative_income(self):
        self.assertIsNone(financial_model(state(),-80,NOW)['forecast'])
    def test_M25_numeric_string_zero_network_does_not_divide_by_zero(self):
        s=state();s['sources']['network']['data']['net_hashrate']='0'
        self.assertIsNone(financial_model(s,80,NOW)['forecast'])
    def test_M26_invalid_pool_fee_does_not_create_negative_projection(self):
        s=state();s['sources']['network']['data']['fee']=2
        self.assertIsNone(financial_model(s,80,NOW)['forecast'])
    def test_M27_numeric_string_quote_converts_consistently(self):
        s=state();s['sources']['price']['data']['pearl-2']['brl']='10'
        self.assertEqual(financial_model(s,80,NOW)['accumulated_brl'],100)
    def test_M28_future_income_sample_is_excluded(self):
        s=state();s['snapshots'].append({'timestamp':NOW+100,'total_prl':999})
        self.assertEqual(financial_model(s,80,NOW)['periods']['day']['prl'],1)
    def test_M29_invalid_snapshot_is_skipped_without_losing_valid_income(self):
        s=state();s['snapshots']+=[None,{}, {'timestamp':'bad','total_prl':3}]
        self.assertEqual(financial_model(s,80,NOW)['periods']['day']['prl'],1)
    def test_M30_duplicate_snapshot_uses_last_observation(self):
        s=state();s['snapshots'].insert(1,{'timestamp':NOW-100,'total_prl':8})
        self.assertEqual(financial_model(s,80,NOW)['periods']['day']['prl'],2)
    def test_M31_bom_cache_is_restored(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d,'finance-cache.json').write_text(json.dumps({'wallet':AUTHOR_WALLET,'sources':{},'snapshots':[],'marker':'kept'}),encoding='utf-8-sig')
            self.assertEqual(FinanceCollector(Path(d),wallet=AUTHOR_WALLET).state.get('marker'),'kept')
    def test_M32_array_cache_does_not_crash_collector_construction(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d,'finance-cache.json').write_text('[]')
            self.assertEqual(FinanceCollector(Path(d),wallet=AUTHOR_WALLET).state['sources'],{})
    def test_M33_malformed_observed_rate_point_is_skipped(self):
        self.assertEqual(observed_rate({'snapshots':[None]},NOW),(None,None))
    def test_M34_malformed_log_counter_does_not_discard_hashrate(self):
        self.assertEqual(parse_gpu_log(log(tail='A:1.2.3 R:0 HW:0'),'GPU-x',0,NOW)['hashrate'],80)
    def test_M35_timestampless_log_line_does_not_replace_current_hash(self):
        parsed=parse_gpu_log('GPU0 RTX: 999 TH/s [A:1 R:0 HW:0]','GPU-x',0,NOW)
        self.assertIsNone(parsed['hashrate'])
    def test_M36_invalid_rate_does_not_replace_previous_measurement(self):
        self.assertEqual(parse_gpu_log(log()+'\n'+log('...',NOW+1),'GPU-x',0,NOW)['hashrate'],80)
    def test_M37_empty_nvidia_response_has_controlled_error(self):
        with self.assertRaises(RuntimeError):read_sample(detected(1)[0],command=lambda args:'')
    def test_M38_other_vendor_invalid_index_does_not_block_nvidia_detection(self):
        result=resolve_devices([{'vendor':'AMD','index':'N/A'},*detected(1)],{},'logs')
        self.assertEqual(len(result),1)
    def test_M39_invalid_gpu_override_can_be_repaired_explicitly(self):
        result=update_gpu_settings({'gpu_settings':{'GPU-x':None}},'GPU-x',enabled=True)
        self.assertTrue(result['gpu_settings']['GPU-x']['enabled'])
    def test_M40_false_wallet_override_does_not_silently_route_to_author(self):
        devices=resolve_devices(detected(1),{'gpu_settings':{'GPU-test-0':{'wallet':False}}},'logs')
        self.assertFalse(devices[0]['enabled'])
    def test_M41_matching_uuid_prefixes_have_distinct_worker_names(self):
        devices=detected(2)
        devices[0]['uuid']='GPU-1234567890123456-a';devices[1]['uuid']='GPU-1234567890123456-b'
        result=resolve_devices(devices,{},'logs')
        self.assertNotEqual(result[0]['worker_name'],result[1]['worker_name'])
    def test_M42_nan_hash_is_unknown_not_a_mining_gpu(self):
        d={'enabled':True,'miner_running':True,'miner':{'hashrate':float('nan'),'hashrate_at':NOW}}
        self.assertIsNone(fresh_hash(d,NOW));self.assertEqual(totals([d],NOW)['mining_count'],0)
    def test_M43_missing_gpu_power_marks_the_total_partial(self):
        self.assertTrue(totals([{'gpu':{'power.draw':100}},{'gpu':{}}],NOW)['power_partial'])
    def test_M44_invalid_gpu_temperature_does_not_poison_rig_maximum(self):
        self.assertEqual(totals([{'gpu':{'temperature.gpu':70}},{'gpu':{'temperature.gpu':999}}],NOW)['max_temperature_c'],70)
    def test_M45_chart_rejects_non_numeric_sample_without_tk_exception(self):
        chart=SimpleNamespace(points=deque([(100,75)],maxlen=900))
        Chart.add(chart,'bad',80);Chart.add(chart,101,'bad')
        self.assertEqual(list(chart.points),[(100,75)])
    def test_M46_false_cannot_display_as_one_real_of_income(self):
        self.assertIsNone(safe_number(True))
    def test_M47_invalid_sensor_format_is_unknown_instead_of_ui_exception(self):
        self.assertEqual(fmt('N/A',' W'),'—')
    def test_M48_future_monitor_snapshot_is_not_reported_as_current(self):
        with patch('monitor.time.time',return_value=NOW):
            self.assertIn('MONITOR SEM ATUALIZACAO',console_text({'collected_at':NOW+100},{}))
    def test_M49_nan_sensor_timestamp_cannot_validate_a_benchmark_sample(self):
        with tempfile.TemporaryDirectory() as d:
            agent=GPUAgent(d);gpu=detected(1)[0]
            try:
                agent.start_benchmark(gpu,300,now=NOW)
                result=agent.observe(gpu,{**sensor(),'sampled_at':float('nan')},80,now=NOW,hash_sampled_at=NOW)
                self.assertEqual(result['invalid_samples'],1)
            finally:agent.close()
    def test_M50_invalid_share_counter_does_not_crash_benchmark_collection(self):
        with tempfile.TemporaryDirectory() as d:
            agent=GPUAgent(d);gpu=detected(1)[0]
            try:
                agent.start_benchmark(gpu,300,now=NOW)
                result=agent.observe(gpu,sensor(),80,accepted=float('inf'),now=NOW,hash_sampled_at=NOW)
                self.assertEqual(result['invalid_samples'],1)
            finally:agent.close()

    def test_M51_corrupt_control_timestamp_does_not_hide_all_gpu_sensors(self):
        from gpu_runtime import RigTelemetry
        from rig_config import gpu_paths
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);logs=root/'logs';paths=gpu_paths(logs,'GPU-test-0')
            directory=Path(paths['state_dir']);directory.mkdir(parents=True)
            (directory/'gpu-agent-control.json').write_text('{"sampled_at":"bad"}')
            (root/'rig-manager.json').write_text('{"sampled_at":"bad","devices":[]}')
            reader=RigTelemetry();reader.detected=detected(1);reader.detected_at=NOW
            with patch('gpu_runtime.STATE_DIR',root),patch('gpu_runtime.LOG_DIR',logs),patch('gpu_runtime.read_settings',return_value={}),patch('gpu_runtime.read_gpu_samples',return_value={}),patch('gpu_runtime.time.time',return_value=NOW):
                result=reader.sample(set())
            self.assertEqual(len(result['devices']),1)
            self.assertTrue(result['devices'][0]['thermal_control']['stale'])

    def test_M52_array_runtime_status_is_not_treated_as_mapping(self):
        from gpu_runtime import read_json
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'state.json';path.write_text('[]')
            self.assertEqual(read_json(path),{})

    def test_M53_pending_history_payload_cannot_be_mutated_after_enqueue(self):
        with tempfile.TemporaryDirectory() as d:
            store=HistoryStore(Path(d)/'history.sqlite3',AUTHOR_WALLET)
            try:
                self.assertTrue(store.flush())
                with closing(sqlite3.connect(store.path)) as lock:
                    lock.execute('BEGIN IMMEDIATE')
                    payload={'timestamp':1,'key':'immutable','kind':'audit','data':{'value':1}}
                    store.submit('event',payload);payload['data']['value']=999
                    lock.rollback()
                self.assertTrue(store.flush())
                self.assertEqual(json.loads(store.read("select data_json from events where event_key='immutable'")[0][0])['value'],1)
            finally:store.close()

    def test_M54_status_event_uses_the_sample_wallet(self):
        with tempfile.TemporaryDirectory() as d:
            store=HistoryStore(Path(d)/'history.sqlite3','default')
            try:
                store.telemetry({'wallet':'other','collected_at':1,'miner':{},'miner_running':True})
                self.assertTrue(store.flush())
                self.assertEqual(store.read("select wallet from events where kind='status_changed'")[0][0],'other')
            finally:store.close()

    def test_M55_finance_error_event_uses_the_queried_wallet(self):
        with tempfile.TemporaryDirectory() as d:
            store=HistoryStore(Path(d)/'history.sqlite3','default')
            try:
                store.finance({'wallet':'other','attempted_at':1,'errors':{'balance':'offline'}})
                self.assertTrue(store.flush())
                self.assertEqual(store.read("select wallet from events where kind='finance_error'")[0][0],'other')
            finally:store.close()

    def test_M56_finance_read_failure_keeps_requested_wallet_identity(self):
        with tempfile.TemporaryDirectory() as d:
            store=HistoryStore(Path(d)/'history.sqlite3','default')
            try:
                with patch.object(store,'read',side_effect=sqlite3.OperationalError('busy')):
                    self.assertEqual(store.finance_state(0,wallet='other')['wallet'],'other')
            finally:store.close()

    def test_M57_gpu_history_keeps_nested_complete_identity(self):
        with tempfile.TemporaryDirectory() as d:
            store=HistoryStore(Path(d)/'history.sqlite3',AUTHOR_WALLET)
            try:
                store.rig_telemetry({'devices':[{'uuid':'GPU-x','collected_at':1,'identity':{'vbios_version':'fixture'},'miner':{}}]})
                self.assertTrue(store.flush())
                self.assertEqual(json.loads(store.read('select identity_json from gpu_devices')[0][0])['vbios_version'],'fixture')
            finally:store.close()

    def test_M58_invalid_calendar_date_does_not_abort_log_archival(self):
        with tempfile.TemporaryDirectory() as d:
            store=HistoryStore(Path(d)/'history.sqlite3',AUTHOR_WALLET)
            try:
                path=Path(d)/'miner.log';path.write_text('[2026-99-25 00:00:00] fixture\n')
                store.archive_log(path);self.assertTrue(store.flush())
                self.assertEqual(store.read("select count(*) from events where kind='miner_log'")[0][0],1)
            finally:store.close()

    def test_M59_non_ascii_key_cannot_leak_through_header_encoding_error(self):
        with patch('ai_advisor.build_opener') as opener:
            with self.assertRaisesRegex(ValueError,'Chave inválida'):request_json('https://example',{},'private-\u2603')
            opener.assert_not_called()

    def test_M60_nan_thermal_import_is_reported_without_poisoning_history(self):
        with tempfile.TemporaryDirectory() as d:
            store=HistoryStore(Path(d)/'history.sqlite3',AUTHOR_WALLET)
            try:
                path=Path(d)/'thermal.jsonl';path.write_text('{"sampled_at":NaN}\n')
                store.archive_thermal(path);self.assertTrue(store.flush())
                self.assertEqual(store.read("select count(*) from events where kind='thermal_import_error'")[0][0],1)
            finally:store.close()
