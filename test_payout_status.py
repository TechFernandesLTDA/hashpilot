import copy
import unittest
from monitor import payout_for_local_state


class PayoutStatusTests(unittest.TestCase):
    def test_stopped_rig_with_shortfall_does_not_promise_historical_eta(self):
        model={'wallet':'own','payout':{'eta_label':'3 h','remaining_prl':.01,'confirmed_prl':.69}}
        original=copy.deepcopy(model)
        result=payout_for_local_state(model,{'rig':{'devices':[{'wallet':'own','miner_running':False}]}})
        self.assertIn('Sem previsão local',result['eta_label'])
        self.assertEqual(result['confirmed_prl'],.69)
        self.assertEqual(model,original)

    def test_existing_threshold_can_mature_without_running_miner(self):
        model={'payout':{'eta_label':'Aguardando confirmação','remaining_prl':0}}
        result=payout_for_local_state(model,{'rig':{'devices':[{'miner_running':False}]}})
        self.assertEqual(result,model['payout'])

    def test_other_wallet_gpu_does_not_supply_selected_wallet_eta(self):
        model={'selected_wallet':'selected','payout':{'eta_label':'3 h','remaining_prl':.1}}
        result=payout_for_local_state(model,{'rig':{'devices':[{'wallet':'selected','miner_running':False},{'wallet':'other','miner_running':True}]}})
        self.assertIn('Sem previsão local',result['eta_label'])

    def test_unknown_or_running_state_never_claims_rig_is_stopped(self):
        model={'payout':{'eta_label':'3 h','remaining_prl':.1}}
        for state in (None,True):
            with self.subTest(state=state):self.assertEqual(payout_for_local_state(model,{'rig':{'devices':[{'miner_running':state}]}}),model['payout'])
