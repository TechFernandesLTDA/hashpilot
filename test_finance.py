"""Verificações das regras monetárias e de cobertura de histórico."""
import datetime as dt
import unittest
from unittest.mock import patch
from finance import LOCAL_TZ, financial_model


class FinanceRules(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 10, 24, 20, tzinfo=LOCAL_TZ).timestamp()
        self.state = {"sources": {
            "price": {"fetched_at": self.now, "data": {"pearl-2": {"brl": 10}}},
            "balance": {"fetched_at": self.now, "data": {"total": 2, "unconfirmed": 1}},
            "payouts": {"fetched_at": self.now, "data": {"paid": 8}},
            "network": {"fetched_at": self.now, "data": {"net_hashrate": 100e12, "block_reward": 2, "block_time": 86400, "fee": .03}},
        }, "snapshots": []}

    def test_missing_history_is_unknown_not_zero(self):
        model = financial_model(self.state, None, self.now)
        self.assertIsNone(model["periods"]["day"]["brl"])
        self.assertEqual(model["accumulated_brl"], 100)

    def test_payout_transfer_does_not_create_income(self):
        self.state["snapshots"] = [{"timestamp": self.now-120, "total_prl": 10}, {"timestamp": self.now, "total_prl": 10}]
        model = financial_model(self.state, None, self.now)
        self.assertEqual(model["periods"]["day"]["prl"], 0)
        self.assertTrue(model["periods"]["day"]["partial"])

    def test_local_midnight_and_week_boundaries(self):
        self.state["snapshots"] = [
            {"timestamp": dt.datetime(2026, 10, 18, 23, 59, tzinfo=LOCAL_TZ).timestamp(), "total_prl": 1},
            {"timestamp": dt.datetime(2026, 10, 23, 23, 59, tzinfo=LOCAL_TZ).timestamp(), "total_prl": 4},
            {"timestamp": self.now, "total_prl": 10}]
        model = financial_model(self.state, None, self.now)
        self.assertEqual(model["periods"]["day"]["prl"], 6)
        self.assertEqual(model["periods"]["week"]["prl"], 9)
        self.assertFalse(model["periods"]["day"]["partial"])
        self.assertTrue(model["periods"]["month"]["partial"])

    def test_forecast_deducts_fees_and_revalues_price(self):
        model = financial_model(self.state, 50, self.now)
        self.assertAlmostEqual(model["forecast"]["prl_day"], .97*.98)
        self.assertAlmostEqual(model["forecast"]["brl_7days"], .97*.98*10*7)
        self.state["sources"]["price"]["data"]["pearl-2"]["brl"] = 20
        updated = financial_model(self.state, 50, self.now)
        self.assertEqual(updated["accumulated_brl"], 200)

    def test_support_reduces_forecast_but_never_observed_pool_credits(self):
        wallet='prl1'+'q'*60
        self.state['snapshots']=[{'timestamp':self.now-120,'total_prl':9},{'timestamp':self.now,'total_prl':10}]
        with patch('finance.WALLET',wallet),patch('finance.SETTINGS',{'wallet':wallet,'support_enabled':True}):
            model=financial_model(self.state,50,self.now)
        self.assertAlmostEqual(model['forecast']['prl_day'],.97*.98*.95)
        self.assertEqual(model['forecast']['support_fraction'],.05)
        self.assertEqual(model['periods']['day']['prl'],1)
        self.assertEqual(model['accumulated_prl'],10)
        with patch('finance.WALLET',wallet),patch('finance.SETTINGS',{'wallet':wallet,'support_enabled':False}):
            disabled=financial_model(self.state,50,self.now)
        self.assertAlmostEqual(disabled['forecast']['prl_day'],.97*.98)

    def test_days_closed_do_not_attribute_old_income_to_day_or_week(self):
        self.state["snapshots"] = [
            {"timestamp": dt.datetime(2026, 10, 17, 20, tzinfo=LOCAL_TZ).timestamp(), "total_prl": 1},
            {"timestamp": self.now-120, "total_prl": 9},
            {"timestamp": self.now, "total_prl": 10}]
        model = financial_model(self.state, None, self.now)
        for period in ("day", "week"):
            self.assertTrue(model["periods"][period]["partial"])
            self.assertEqual(model["periods"][period]["prl"], 1)
            self.assertEqual(model["periods"][period]["since"], self.now-120)

    def test_first_sample_after_long_gap_is_unknown(self):
        self.state["snapshots"] = [
            {"timestamp": self.now-8*86400, "total_prl": 1},
            {"timestamp": self.now, "total_prl": 10}]
        model = financial_model(self.state, None, self.now)
        for period in ("day", "week"):
            self.assertTrue(model["periods"][period]["partial"])
            self.assertIsNone(model["periods"][period]["prl"])


if __name__ == "__main__":
    unittest.main()
