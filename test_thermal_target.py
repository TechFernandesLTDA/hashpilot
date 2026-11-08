import json
from pathlib import Path
import tempfile
import unittest
from thermal_target import MEMORY_SETTLE_S, MemoryExplorer, TargetTemperatureController, clamp_target
from gpu_agent import read_fan, read_target_temperature


class ClampTests(unittest.TestCase):
    def test_target_is_bounded_below_hard_limit(self):
        self.assertEqual(clamp_target(90, 83), 80)
        self.assertEqual(clamp_target(30, 83), 50)
        self.assertEqual(clamp_target("72", 83), 72)
        self.assertIsNone(clamp_target(None, 83))
        self.assertIsNone(clamp_target("abc", 83))
        self.assertIsNone(clamp_target(float("nan"), 83))


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.c = TargetTemperatureController(72)

    def test_above_target_with_saturated_fan_reduces(self):
        self.assertEqual(self.c.step(73.5, 100, 1650, 0).action, "clock_down")

    def test_above_target_with_fan_headroom_holds(self):
        self.assertEqual(self.c.step(73.5, 60, 1650, 0).action, "hold")

    def test_far_above_target_reduces_even_without_saturated_fan(self):
        self.assertEqual(self.c.step(74.5, 60, 1650, 0).action, "clock_down")

    def test_down_steps_respect_interval(self):
        self.assertEqual(self.c.step(73.5, 100, 1650, 0).action, "clock_down")
        self.c.notify_applied("clock_down", 1650, 1635, 0)
        self.assertEqual(self.c.step(73.5, 100, 1635, 2).action, "hold")
        self.assertEqual(self.c.step(73.5, 100, 1635, 6).action, "clock_down")

    def test_deadband_holds(self):
        for temp in (70.5, 71, 72, 72.5):
            self.assertEqual(self.c.step(temp, 100, 1650, 0).action, "hold", temp)

    def test_sustained_headroom_raises(self):
        self.assertEqual(self.c.step(68, 80, 1650, 0).action, "hold")
        self.assertEqual(self.c.step(68, 80, 1650, 10).action, "hold")
        self.assertEqual(self.c.step(68, 80, 1650, 20).action, "clock_up")

    def test_overshoot_after_raise_learns_ceiling(self):
        self.c.step(68, 80, 1650, 0)
        self.assertEqual(self.c.step(68, 80, 1650, 20).action, "clock_up")
        self.c.notify_applied("clock_up", 1650, 1665, 20)
        self.assertEqual(self.c.step(73.5, 100, 1665, 40).action, "clock_down")
        self.assertEqual(self.c.ceiling_mhz, 1650)
        self.c.notify_applied("clock_down", 1665, 1650, 40)
        # Esfriou de novo: nÃ£o volta a subir acima do teto aprendido.
        for t in range(42, 200, 2):
            self.assertEqual(self.c.step(68, 80, 1650, t).action, "hold")

    def test_ceiling_expires(self):
        self.c._learn_ceiling(1650, 0)
        self.c.step(68, 80, 1650, 601)
        self.assertEqual(self.c.step(68, 80, 1650, 640).action, "clock_up")

    def test_raise_without_hashrate_gain_steps_back(self):
        self.c.record_hashrate(1650, 75.0)
        self.c.step(68, 80, 1650, 0, hashrate=75.0)
        self.assertEqual(self.c.step(68, 80, 1650, 20, hashrate=75.0).action, "clock_up")
        self.c.notify_applied("clock_up", 1650, 1665, 20, rate_before=75.0)
        for t in range(22, 64, 2):
            self.c.step(68, 80, 1665, t, hashrate=75.05)
        self.assertEqual(self.c.step(68, 80, 1665, 66, hashrate=75.05).action, "clock_down")
        self.assertEqual(self.c.ceiling_mhz, 1650)

    def test_raise_with_gain_keeps_climbing(self):
        self.c.step(68, 80, 1650, 0, hashrate=75.0)
        self.c.step(68, 80, 1650, 20, hashrate=75.0)
        self.c.notify_applied("clock_up", 1650, 1665, 20, rate_before=75.0)
        for t in range(22, 66, 2):
            self.c.step(68, 80, 1665, t, hashrate=76.0)
        self.assertIsNone(self.c.ceiling_mhz)
        self.assertEqual(self.c.step(68, 80, 1665, 90, hashrate=76.0).action, "clock_up")

    def test_missing_sensor_holds(self):
        self.assertEqual(self.c.step(None, 100, 1650, 0).action, "hold")


class SettingsTests(unittest.TestCase):
    def test_per_gpu_override_wins_over_global(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text(json.dumps({"target_temperature_c": 70, "gpu_settings": {"GPU-a": {"target_temperature_c": 68}}}), encoding="utf-8")
            self.assertEqual(read_target_temperature(path, "GPU-a"), 68)
            self.assertEqual(read_target_temperature(path, "GPU-b"), 70)
            path.write_text("{}", encoding="utf-8")
            self.assertIsNone(read_target_temperature(path, "GPU-a"))
            path.write_text("  ", encoding="utf-8")
            self.assertIsNone(read_target_temperature(path, "GPU-a"))

    def test_fan_reading_is_tolerant(self):
        gpu = {"uuid": "GPU-a"}
        self.assertEqual(read_fan(gpu, command=lambda a: "93\n"), 93)
        self.assertIsNone(read_fan(gpu, command=lambda a: "[N/A]\n"))
        def broken(a): raise RuntimeError("smi")
        self.assertIsNone(read_fan(gpu, command=broken))


if __name__ == "__main__":
    unittest.main()


class MemoryExplorerTests(unittest.TestCase):
    def run_round(self, explorer, rates, start=0.0, temp=75, errors=0):
        """Simula amostras a cada 2 s; rates: clock -> hashrate medido naquele clock."""
        applied=[];now=start
        for _ in range(2000):
            wanted=explorer.step(now,temp,rates.get(explorer.current_mhz),errors)
            if wanted is not None:applied.append(wanted)
            if not explorer.queue and explorer.best_mhz is not None and explorer.current_mhz==explorer.best_mhz and applied:
                break
            now+=2
        return applied,now

    def test_picks_memory_with_highest_hashrate(self):
        e=MemoryExplorer([6801,5001,405],78)
        applied,_=self.run_round(e,{6801:75.7,5001:80.1,405:79.0})
        self.assertEqual(e.best_mhz,5001)
        self.assertEqual(applied[-1],5001)
        self.assertEqual(set(e.scores),{6801.0,5001.0,405.0})

    def test_hot_or_erroring_candidate_is_disqualified(self):
        e=MemoryExplorer([6801,5001],78)
        applied,_=self.run_round(e,{6801:90.0,5001:80.0},temp=80)
        self.assertEqual(e.scores,{6801.0:0.0,5001.0:0.0})
        e=MemoryExplorer([6801,5001],78)
        e._start_round(0);first=e._next(0)
        settle=MEMORY_SETTLE_S+10
        e.step(settle,75,90,0)          # fim da acomodação: base de erros = 0
        self.assertEqual(e.step(settle+2,75,90,1),5001.0)  # erro novo -> próximo candidato
        self.assertEqual(e.scores[first],0.0)

    def test_starts_with_remembered_best_and_reexplores_after_six_hours(self):
        e=MemoryExplorer([6801,5001,405],78,best_mhz=5001)
        self.assertEqual(e.step(0,75,80,0),5001.0)
        _,end=self.run_round(e,{6801:75.0,5001:80.0,405:79.0},start=2)
        self.assertIsNone(e.step(end+10,75,80,0))
        e.step(end+6*3600+1,75,80,0)
        self.assertTrue(e.queue)

    def test_single_memory_clock_never_changes(self):
        self.assertIsNone(MemoryExplorer([5001],78).step(0,75,80,0))


class MemoryUnreachableTests(unittest.TestCase):
    def test_candidate_capped_by_driver_is_skipped(self):
        # Série 30 em CUDA (P2): pedir 7001 deixa a memória em 6801; não vale medir.
        e=MemoryExplorer([7001,6801,5001],78)
        self.assertEqual(e.step(0,75,80,0,6801),7001.0)
        self.assertIsNone(e.step(4,75,80,0,6801))
        self.assertEqual(e.step(12,75,80,0,6801),6801.0)
        self.assertIn(7001.0,e.unreachable)
        self.assertNotIn(7001.0,e.scores)
        self.assertIsNone(e.step(14,75,80,0,6801))  # 6801 aplicado: segue medindo
        e._start_round(100)
        self.assertNotIn(7001.0,e.queue)


class ProfitObjectiveTests(unittest.TestCase):
    def test_profit_prefers_low_power_memory_when_hashrate_is_close(self):
        # Medido em 07/11/2026: 6801 -> 75,8 TH/s @170 W; 810 -> 75,25 TH/s @152 W. R$0,11/TH/s/dia, R$0,90/kWh.
        def run(objective):
            e=MemoryExplorer([6801,810],78,objective=objective,revenue_per_ths_day=0.1104,price_kwh=0.90)
            data={6801:(75.8,170.0),810:(75.25,152.0)};now=0.0
            for _ in range(3000):
                rate,watts=data.get(e.current_mhz,(None,None))
                e.step(now,75,rate,0,None,watts)
                if not e.queue and e.best_mhz is not None and e.current_mhz==e.best_mhz and e.scores:break
                now+=2
            return e
        self.assertEqual(run("hashrate").best_mhz,6801)
        self.assertEqual(run("profit").best_mhz,810)

    def test_profit_without_price_scores_hashrate(self):
        e=MemoryExplorer([6801,810],78,objective="profit",revenue_per_ths_day=0.11,price_kwh=None)
        self.assertEqual(e.score(75.0,170.0),75.0)
        e.price_kwh=0.9
        self.assertAlmostEqual(e.score(75.0,170.0),75*0.11-170*24/1000*0.9)


class OptimizationSettingsTests(unittest.TestCase):
    def test_profit_requires_price_and_revenue(self):
        from gpu_agent import read_optimization
        with tempfile.TemporaryDirectory() as folder:
            settings=Path(folder)/"settings.json";finance=Path(folder)/"finance-latest.json"
            finance.write_text(json.dumps({"forecast":{"brl_day":8.32,"hashrate_ths":75.36}}),encoding="utf-8")
            settings.write_text(json.dumps({"optimize_for":"profit","electricity_price_brl_kwh":0.9}),encoding="utf-8")
            objective,price,revenue=read_optimization(settings,finance)
            self.assertEqual((objective,price),("profit",0.9))
            self.assertAlmostEqual(revenue,8.32/75.36)
            settings.write_text(json.dumps({"optimize_for":"profit"}),encoding="utf-8")
            self.assertEqual(read_optimization(settings,finance)[0],"hashrate")
            finance.write_text("  ",encoding="utf-8")
            settings.write_text(json.dumps({"optimize_for":"profit","electricity_price_brl_kwh":0.9}),encoding="utf-8")
            self.assertEqual(read_optimization(settings,finance)[0],"hashrate")


class RememberedUnreachableTests(unittest.TestCase):
    def test_remembered_best_that_driver_caps_is_dropped(self):
        e=MemoryExplorer([7001,6801,5001],78,best_mhz=7001)
        self.assertEqual(e.step(0,75,80,0,6801),7001.0)
        self.assertEqual(e.step(12,75,80,0,6801),6801.0)
        self.assertIsNone(e.best_mhz)
        self.assertIn(7001.0,e.unreachable)
        self.assertEqual(e.queue,[5001.0])


class MemoryTieTests(unittest.TestCase):
    def test_hashrate_tie_prefers_lower_power(self):
        # 07/11/2026: 6801 -> 75,34 TH/s @164 W; 810 -> 75,23 TH/s @152 W: empate, fica 810.
        e=MemoryExplorer([6801,810],78)
        data={6801:(75.34,164.0),810:(75.23,152.0)};now=0.0
        for _ in range(3000):
            rate,watts=data.get(e.current_mhz,(None,None))
            e.step(now,75,rate,0,None,watts)
            if not e.queue and e.best_mhz is not None and e.current_mhz==e.best_mhz and len(e.scores)==2:break
            now+=2
        self.assertEqual(e.best_mhz,810.0)
