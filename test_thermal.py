"""Verificações de histerese e rampas; nenhuma chamada à GPU."""
import math
import unittest
from thermal import ThermalPolicy


class ThermalRules(unittest.TestCase):
    def test_hot_reductions_obey_cooldown_and_step(self):
        policy = ThermalPolicy()
        self.assertEqual(policy.step(78, 0).clock_mhz, 1725)
        self.assertEqual(policy.step(79, 2).clock_mhz, 1725)
        self.assertEqual(policy.step(79, 5).clock_mhz, 1725)
        self.assertEqual(policy.step(79, 6).clock_mhz, 1710)
        self.assertEqual(policy.step(80, 8).clock_mhz, 1710)
        self.assertEqual(policy.step(80, 10).clock_mhz, 1650)

    def test_emergency_is_immediate_even_inside_cooldown(self):
        policy = ThermalPolicy()
        policy.step(78, 0)
        decision = policy.step(83, 1)
        self.assertEqual((decision.clock_mhz, decision.mode), (1500, "emergency"))
        self.assertEqual(policy.step(84, 2).clock_mhz, 1500)

    def test_sudden_heating_is_anticipated_but_not_slow_drift(self):
        policy = ThermalPolicy()
        policy.step(76, 0)
        decision = policy.step(77, 2)
        self.assertEqual((decision.clock_mhz, decision.mode), (1710, "anticipating"))
        self.assertEqual(policy.step(77.9, 4).clock_mhz, 1710)
        other = ThermalPolicy()
        other.step(76, 0)
        self.assertEqual(other.step(77, 10).clock_mhz, 1740)

    def test_recovery_requires_60_seconds_then_20_second_intervals(self):
        policy = ThermalPolicy(initial_clock=1650)
        policy.step(75, 0)
        self.assertEqual(policy.step(74, 59).clock_mhz, 1650)
        self.assertEqual(policy.step(75, 60).clock_mhz, 1665)
        self.assertEqual(policy.step(75, 79).clock_mhz, 1665)
        self.assertEqual(policy.step(75, 80).clock_mhz, 1680)

    def test_reheating_resets_cold_timer_and_never_increases_hot_clock(self):
        policy = ThermalPolicy(initial_clock=1650)
        policy.step(75, 0)
        policy.step(75, 58)
        self.assertEqual(policy.step(75.1, 59).clock_mhz, 1650)
        policy.step(75, 60)
        self.assertEqual(policy.step(75, 119).clock_mhz, 1650)
        self.assertEqual(policy.step(75, 120).clock_mhz, 1665)
        self.assertEqual(policy.step(77, 122).clock_mhz, 1665)

    def test_clock_is_clamped_and_holding_band_does_not_oscillate(self):
        policy = ThermalPolicy(initial_clock=3000)
        self.assertEqual(policy.clock_mhz, 1800)
        for now in range(0, 200, 2):
            self.assertEqual(policy.step(76, now).clock_mhz, 1800)
        cold = ThermalPolicy(initial_clock=1795)
        cold.step(74, 0)
        self.assertEqual(cold.step(74, 60).clock_mhz, 1800)
        hot = ThermalPolicy(initial_clock=1510)
        self.assertEqual(hot.step(80, 0).clock_mhz, 1500)

    def test_invalid_sensor_and_backwards_time_are_rejected_without_state_change(self):
        policy = ThermalPolicy()
        policy.step(74, 10)
        for invalid in (None, math.nan, math.inf, -1, 151, True, "unavailable"):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                policy.step(invalid, 12)
            self.assertEqual(policy.clock_mhz, 1740)
        with self.assertRaises(ValueError):
            policy.step(78, 9)
        self.assertEqual(policy.step(74, 12).clock_mhz, 1740)


if __name__ == "__main__":
    unittest.main()
