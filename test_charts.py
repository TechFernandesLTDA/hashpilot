"""Regressões de ingestão tardia e lacunas normais do log de hashrate."""
from collections import deque
from types import SimpleNamespace
import time
import tkinter as tk
import unittest
from monitor import Chart

class HashrateChartTests(unittest.TestCase):
    def chart(self, points=(), max_gap=180):
        return SimpleNamespace(points=deque(points, maxlen=900), max_gap=max_gap)

    def test_late_measurement_is_retained_after_unavailable_marker(self):
        chart = self.chart([(100, 75), (200, None), (202, None)])
        Chart.add(chart, 201, 80)
        self.assertEqual(list(chart.points), [(100, 75), (200, None), (201, 80), (202, None)])
        self.assertEqual(Chart.sample_info(chart)["latest_sample_at"], 201)
        self.assertEqual(Chart.sample_info(chart)["latest_value"], 80)

    def test_missing_marker_does_not_overwrite_real_sample(self):
        chart = self.chart([(100, 75)])
        Chart.add(chart, 100, None)
        Chart.add(chart, 100, 76)
        self.assertEqual(list(chart.points), [(100, 76)])

    def test_ninety_second_miner_intervals_form_a_continuous_line(self):
        points = [(100, 75), (190, 76), (280, 77)]
        self.assertEqual(Chart.segments(self.chart(points)), [points])

    def test_long_outage_and_explicit_missing_data_break_the_line(self):
        self.assertEqual(Chart.segments(self.chart([(100, 75), (281, 76)])), [[(100, 75)], [(281, 76)]])
        self.assertEqual(Chart.segments(self.chart([(100, 75), (110, None), (120, 76)])), [[(100, 75)], [(120, 76)]])

    def test_late_arrival_does_not_exceed_bounded_history(self):
        chart = self.chart((i, 75) for i in range(900))
        Chart.add(chart, 450.5, 80)
        self.assertEqual(len(chart.points), 900)
        self.assertIn((450.5, 80), chart.points)
        self.assertEqual(chart.points[-1], (899, 75))

    def test_nonfinite_data_cannot_poison_plot_scaling(self):
        chart = self.chart([(100, 75)])
        Chart.add(chart, 101, float("nan"))
        Chart.add(chart, float("inf"), 80)
        self.assertEqual(list(chart.points), [(100, 75)])

class ChartRenderingTests(unittest.TestCase):
    def test_repeated_redraws_keep_canvas_items_bounded(self):
        root = tk.Tk()
        root.withdraw()
        try:
            chart = Chart(root, "Hashrate", "#00aa88", "TH/s")
            # Exercise a real Tcl canvas without mapping a window on the desktop.
            chart.winfo_width = lambda: 600
            chart.winfo_height = lambda: 300
            chart.add(time.time()-30, 75)
            chart.draw(force=True)
            initial_count = len(chart.find_all())
            for _ in range(100):
                chart.draw(force=True)
            self.assertEqual(len(chart.find_all()), initial_count)
            self.assertEqual(len(chart.find_withtag("time-axis")), 2)
            chart.points.clear()
            chart.draw(force=True)
            self.assertEqual(len(chart.find_withtag("time-axis")), 0)
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
