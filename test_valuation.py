import unittest
from valuation import value_in_currencies

class CurrencyRules(unittest.TestCase):
    def test_real_currency_quotes_convert_without_assuming_usd_equals_brl(self):
        values = value_in_currencies(2, 10, 2, 100000, 500000)
        self.assertEqual(values, {"prl": 2, "brl": 20, "usd": 4, "btc": .00004})

    def test_unknown_quote_is_unknown_not_zero_and_brl_cross_rate_is_available(self):
        values = value_in_currencies(2, 10, None, None, 500000)
        self.assertIsNone(values["usd"])
        self.assertEqual(values["btc"], .00004)
        self.assertEqual(value_in_currencies(None, 10, 2, 100000), {"prl": None, "brl": None, "usd": None, "btc": None})

    def test_pool_corrections_keep_their_sign_and_zero_is_a_known_amount(self):
        self.assertEqual(value_in_currencies(-1, 10, 2, 100000)["btc"], -.00002)
        self.assertEqual(value_in_currencies(0, 10, 2, 100000)["btc"], 0)

    def test_invalid_and_zero_exchange_rates_never_create_infinite_btc(self):
        self.assertIsNone(value_in_currencies(1, 10, 2, 0)["btc"])
        self.assertIsNone(value_in_currencies(1, 10, float('nan'), float('inf'))["btc"])
        self.assertIsNone(value_in_currencies(float('nan'), 10, 2, 100000)["prl"])

if __name__ == '__main__':
    unittest.main()
