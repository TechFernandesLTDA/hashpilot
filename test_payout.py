"""Regras de pagamento: mínimo confirmado, estimativas honestas e carteira pública."""
import copy
import unittest
from payout import payout_model

NOW = 100000.0

def state(total=.2, confirmed=0, pending=.2, threshold=1):
    return {"wallet": "test-wallet", "sources": {
        "balance": {"fetched_at": NOW, "data": {"total": total, "confirmed": confirmed, "unconfirmed": pending,
                    "threshold": threshold, "maturation_time": 18000}},
        "network": {"fetched_at": NOW, "data": {"minpay": 1, "blocks_for_maturation": 100, "block_time": 180}},
        "payout_history": {"fetched_at": NOW, "data": {"results": []}}}, "snapshots": [], "errors": {}}

class PayoutRules(unittest.TestCase):
    def test_under_minimum_adds_generation_maturity_and_hourly_cycle(self):
        result = payout_model(state(), {"prl_day": 1}, 10, NOW)
        self.assertAlmostEqual(result["remaining_prl"], .8)
        self.assertAlmostEqual(result["remaining_brl"], 8)
        self.assertEqual(result["confirmed_progress_pct"], 0)
        self.assertAlmostEqual(result["eta_min_seconds"], .8*86400+18000)
        self.assertEqual(result["eta_max_seconds"]-result["eta_min_seconds"], 3600)

    def test_confirmed_minimum_waits_only_for_cycle_not_maturation(self):
        result = payout_model(state(1.2, 1, .2), None, 10, NOW)
        self.assertEqual(result["eta_min_seconds"], 0)
        self.assertEqual(result["eta_max_seconds"], 3600)
        self.assertIn("confirmado atingido", result["state_label"])

    def test_pending_minimum_is_not_marked_ready_or_given_exact_countdown(self):
        result = payout_model(state(1.2, .2, 1), None, 10, NOW)
        self.assertIn("aguardando confirmação", result["state_label"])
        self.assertEqual(result["eta_min_seconds"], 0)
        self.assertEqual(result["eta_max_seconds"], 21600)
        self.assertEqual(result["generation_seconds"], None)

    def test_observed_rate_requires_coverage_and_does_not_count_payout_twice(self):
        data = state()
        data["snapshots"] = [{"timestamp": NOW-3600, "total_prl": 1}, {"timestamp": NOW, "total_prl": 1}]
        self.assertIsNone(payout_model(data, None, 10, NOW)["rate_prl_day"])
        data["snapshots"][-1]["total_prl"] = 1.1
        result = payout_model(data, {"prl_day": 999}, 10, NOW)
        self.assertAlmostEqual(result["rate_prl_day"], 2.4)
        self.assertIn("observado", result["rate_label"])
        data["snapshots"][0]["timestamp"] = NOW-60
        self.assertEqual(payout_model(data, {"prl_day": 2}, 10, NOW)["rate_prl_day"], 2)

    def test_first_zero_balance_includes_maturity_and_unknown_delay_is_not_zero(self):
        data = state(0, 0, 0)
        result = payout_model(data, {"prl_day": 1}, 10, NOW)
        self.assertEqual(result["eta_min_seconds"], 86400+18000)
        del data["sources"]["balance"]["data"]["maturation_time"]
        data["sources"]["network"]["data"] = {"minpay": 1}
        result = payout_model(data, {"prl_day": 1}, 10, NOW)
        self.assertIsNone(result["eta_min_seconds"])
        self.assertEqual(result["generation_seconds"], 86400)
        self.assertIn("confirmação sem prazo", result["eta_label"])

    def test_stale_data_cannot_present_live_eta(self):
        data = state()
        data["sources"]["balance"]["fetched_at"] = NOW-181
        result = payout_model(data, {"prl_day": 1}, 10, NOW)
        self.assertTrue(result["stale"])
        self.assertIsNone(result["eta_min_seconds"])
        self.assertIsNone(result["eta_max_seconds"])

    def test_personal_threshold_and_price_change_do_not_change_prl_eta(self):
        data = state(threshold=5)
        first = payout_model(data, {"prl_day": 1}, 10, NOW)
        second = payout_model(data, {"prl_day": 1}, 20, NOW)
        self.assertEqual(first["threshold_prl"], 5)
        self.assertAlmostEqual(first["progress_pct"], 4)
        self.assertEqual(first["eta_min_seconds"], second["eta_min_seconds"])
        self.assertEqual(second["threshold_brl"], first["threshold_brl"]*2)

    def test_missing_or_invalid_data_is_unknown_and_last_payment_is_not_wallet_confirmation(self):
        empty = payout_model({}, None, None, NOW)
        self.assertIsNone(empty["remaining_prl"])
        self.assertIsNone(empty["eta_min_seconds"])
        data = state()
        data["sources"]["balance"]["data"]["total"] = float("nan")
        data["sources"]["payout_history"]["data"]["results"] = [
            {"date": NOW-100, "received": 1, "txid": "older", "status": "paid"},
            {"date": NOW-10, "received": 1.2, "txid": "latest", "status": "sent"}]
        result = payout_model(data, None, 10, NOW)
        self.assertIsNone(result["remaining_prl"])
        self.assertEqual(result["last_payment"]["txid"], "latest")
        self.assertNotIn("recebido", result["state_label"].lower())

if __name__ == "__main__":
    unittest.main()
