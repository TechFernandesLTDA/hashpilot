import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import time
import unittest
from finance import PortfolioFinanceCollector,SharedMarketFetcher,URLS,portfolio_model,portfolio_snapshots
from contribution import AUTHOR_WALLET

A='prl1'+'q'*40
B='prl1'+'p'*40


def wallet_state(wallet,now):
    return {'wallet':wallet,'sources':{
        'price':{'fetched_at':now,'data':{'pearl-2':{'brl':10,'usd':2},'bitcoin':{'brl':500000,'usd':100000}}},
        'balance':{'fetched_at':now,'data':{'total':2,'confirmed':1,'unconfirmed':1,'threshold':1}},
        'payouts':{'fetched_at':now,'data':{'paid':8}},
        'network':{'fetched_at':now,'data':{'net_hashrate':100e12,'block_reward':2,'block_time':86400,'fee':.03}}},
        'snapshots':[{'timestamp':now-120,'total_prl':9},{'timestamp':now,'total_prl':10}]}


def gpu(index,wallet,now,enabled=True):
    return {'uuid':f'GPU-{index}','wallet':wallet,'enabled':enabled,'miner_running':True,
        'miner':{'hashrate':50,'hashrate_at':now,'paused':False}}


class PortfolioRules(unittest.TestCase):
    def setUp(self): self.now=time.time()

    def test_graph_deduplicates_wallets_and_does_not_invent_baseline_before_all_are_observed(self):
        state={'schema_version':2,'wallets':{A:{'snapshots':[{'timestamp':1,'total_prl':10},{'timestamp':3,'total_prl':11}]},
            B:{'snapshots':[{'timestamp':2,'total_prl':20},{'timestamp':4,'total_prl':22}]}}}
        self.assertEqual(portfolio_snapshots(state,[A,A,B]),[{'timestamp':2,'total_prl':30},{'timestamp':3,'total_prl':31},{'timestamp':4,'total_prl':33}])
        self.assertEqual(portfolio_snapshots(state,[A]),state['wallets'][A]['snapshots'])

    def test_six_gpus_same_wallet_never_multiply_balance_or_observed_income(self):
        state={'schema_version':2,'wallets':{A:wallet_state(A,self.now)}}
        result=portfolio_model(state,[gpu(index,A,self.now) for index in range(6)],self.now,settings={'wallet':A})
        self.assertEqual(result['wallet_count'],1)
        self.assertEqual(result['accumulated_prl'],10)
        self.assertEqual(result['periods']['day']['prl'],1)
        self.assertAlmostEqual(result['forecast']['prl_day'],6*.97*.98)
        self.assertEqual(result['hashrate_by_wallet'][A],300)

    def test_two_wallets_are_summed_once_and_can_be_filtered(self):
        state={'schema_version':2,'wallets':{A:wallet_state(A,self.now),B:wallet_state(B,self.now)}}
        devices=[gpu(0,A,self.now),gpu(1,A,self.now),gpu(2,B,self.now)]
        aggregate=portfolio_model(state,devices,self.now,settings={'wallet':A})
        self.assertEqual(aggregate['accumulated_prl'],20)
        self.assertEqual(aggregate['periods']['day']['prl'],2)
        self.assertEqual(aggregate['scope'],'portfolio')
        self.assertIsNone(aggregate['payout']['threshold_prl'])
        selected=portfolio_model(state,devices,self.now,selected_wallet=B,settings={'wallet':A})
        self.assertEqual(selected['wallet'],B)
        self.assertEqual(selected['accumulated_prl'],10)
        self.assertEqual(selected['hashrate_by_wallet'][A],100)

    def test_unknown_wallet_data_is_not_silently_zero(self):
        state={'schema_version':2,'wallets':{A:wallet_state(A,self.now)}}
        model=portfolio_model(state,[gpu(0,A,self.now),gpu(1,B,self.now)],self.now,settings={'wallet':A})
        self.assertIsNone(model['accumulated_prl'])
        self.assertIsNone(model['periods']['day']['prl'])
        self.assertTrue(model['periods']['day']['partial'])

    def test_disabled_or_stale_gpu_does_not_increase_forecast(self):
        devices=[gpu(0,A,self.now),gpu(1,A,self.now,False),gpu(2,A,self.now-181)]
        state={'schema_version':2,'wallets':{A:wallet_state(A,self.now)}}
        result=portfolio_model(state,devices,self.now,settings={'wallet':A})
        self.assertEqual(result['hashrate_by_wallet'][A],50)
        self.assertTrue(result['forecast_partial'])
        self.assertIsNone(result['forecast'])

    def test_support_fee_uses_effective_wallet_and_never_author_extra(self):
        states={wallet:wallet_state(wallet,self.now) for wallet in (AUTHOR_WALLET,A)}
        result=portfolio_model({'schema_version':2,'wallets':states},[gpu(0,AUTHOR_WALLET,self.now),gpu(1,A,self.now)],self.now,
            settings={'wallet':AUTHOR_WALLET,'support_enabled':True})
        self.assertAlmostEqual(result['forecast']['prl_day'],.97*.98*(1+.95))

    def test_public_market_calls_are_coalesced_but_not_wallet_requests(self):
        calls=[]
        def fetch(url): calls.append(url);return {'value':len(calls)}
        cache=SharedMarketFetcher(fetch)
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(cache,[URLS['price']]*6))
        self.assertEqual(calls.count(URLS['price']),1)
        cache('wallet-A');cache('wallet-A')
        self.assertEqual(calls.count('wallet-A'),2)

    def test_collector_requests_each_distinct_wallet_once(self):
        calls=[]
        def fetch(url):
            calls.append(url)
            if 'simple/price' in url:return {'pearl-2':{'brl':10}}
            if '/balance/' in url:return {'total':1,'confirmed':0,'unconfirmed':1}
            if '/stats' in url:return {'paid':2}
            if '/reward-chart/' in url:return []
            if '/payouts/' in url:return {'results':[]}
            return {'net_hashrate':100}
        with tempfile.TemporaryDirectory() as temporary:
            collector=PortfolioFinanceCollector(Path(temporary),wallets=[A,A,A,B,B],fetcher=fetch)
            state=collector.sample()
            self.assertEqual(set(state['wallets']),{A,B})
            self.assertEqual(len([url for url in calls if '/balance/' in url]),2)
            self.assertEqual(len([url for url in calls if 'simple/price' in url]),1)
