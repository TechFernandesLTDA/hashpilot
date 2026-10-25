from pathlib import Path
import tempfile
import time
import unittest
from history import HistoryStore


class RigHistoryRules(unittest.TestCase):
    def test_six_identical_models_same_timestamp_wallet_are_separate_after_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'history.sqlite3'
            store=HistoryStore(path,'wallet-A')
            now=time.time()
            devices=[{'uuid':f'GPU-{index}','index':index,'name':'NVIDIA GeForce RTX 3070','wallet':'wallet-A','enabled':True,
                'collected_at':now,'gpu':{'temperature.gpu':70+index,'power.draw':150+index},
                'miner':{'hashrate':50+index,'hashrate_at':now},'miner_running':True} for index in range(6)]
            store.rig_telemetry({'devices':devices})
            self.assertTrue(store.flush())
            self.assertEqual(len(store.gpu_stats()),6)
            store.close()
            store=HistoryStore(path,'wallet-B')
            try:
                self.assertTrue(store.flush())
                for index in range(6):
                    restored=store.restore_gpu(f'GPU-{index}',now-1)
                    self.assertEqual(restored['points']['hash'],[(now,50+index)])
                    self.assertEqual(restored['identity']['index'],index)
                self.assertEqual(store.restore_gpu('GPU-1',now-1,wallet='wallet-B')['count'],0)
            finally: store.close()

    def test_financial_history_uses_payload_wallet_not_global_history_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            store=HistoryStore(Path(temporary)/'history.sqlite3','global-wallet')
            try:
                stamp=time.time()
                for wallet,value in (('wallet-A',1),('wallet-B',2)):
                    store.finance({'wallet':wallet,'snapshots':[{'timestamp':stamp,'total_prl':value}],'sources':{}})
                self.assertTrue(store.flush())
                self.assertEqual(store.finance_state(0,wallet='wallet-A')['snapshots'][0]['total_prl'],1)
                self.assertEqual(store.finance_state(0,wallet='wallet-B')['snapshots'][0]['total_prl'],2)
                self.assertEqual(store.finance_state(0)['snapshots'],[])
            finally:store.close()
