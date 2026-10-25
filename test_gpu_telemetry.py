"""Seis GPUs simuladas: identidade, telemetria e logs não podem se misturar."""
import datetime as dt
from pathlib import Path
import tempfile
import unittest

from gpu_telemetry import FIELDS,parse_gpu_log,read_gpu_samples,read_device_payload


def device(index):
    return {"vendor":"NVIDIA","index":index,"uuid":f"GPU-test-{index}","name":f"NVIDIA GeForce RTX {3060+index}"}


def line(index,rate,accepted=10,rejected=0,stamp="2026-10-24 23:00:00"):
    return f"[{stamp}] GPU{index} RTX: {rate} TH/s [T:65C P:150W A:{accepted} R:{rejected} HW:0]"


class RigTelemetryTests(unittest.TestCase):
    def test_six_sensors_single_query_sorted_by_uuid_not_row_order(self):
        devices=[device(index) for index in range(6)]
        commands=[]
        def command(arguments):
            commands.append(arguments)
            rows=[]
            for index in reversed(range(6)):
                rows.append(", ".join(map(str,[f"GPU-test-{index}",index,f"GPU {index}","580.0",60+index,100+index,200,
                    1500+index,5000,100,80,1000,8000,90,"P0"])))
            return "\n".join(rows)
        result=read_gpu_samples(devices,command,now=100)
        self.assertEqual(len(commands),1)
        self.assertIn("uuid,index,name,driver_version",commands[0][0])
        for index in range(6):
            self.assertEqual(result[f"GPU-test-{index}"]["temperature.gpu"],60+index)
            self.assertEqual(result[f"GPU-test-{index}"]["power.draw"],100+index)
            self.assertIsNone(result[f"GPU-test-{index}"]["error"])

    def test_missing_gpu_and_na_sensor_are_not_zero(self):
        values=["GPU-test-0",0,"RTX 3070","580","N/A",100,200,1500,5000,100,90,1000,8000,100,"P0"]
        result=read_gpu_samples([device(0),device(1)],lambda _: ",".join(map(str,values)),now=100)
        self.assertIsNone(result["GPU-test-0"]["temperature.gpu"])
        self.assertIsNotNone(result["GPU-test-0"]["error"])
        self.assertIn("não apareceu",result["GPU-test-1"]["error"])

    def test_physical_index_in_shared_log_does_not_take_total_or_other_cards(self):
        text="\n".join([line(index,10+index,100+index,index) for index in range(6)])
        text+="\n[2026-10-24 23:00:00] Total: 999 TH/s [A:999 R:99 HW:0]"
        text+="\n[2026-10-24 23:00:00] Average hashrate: 1m 999 TH/s | 15m 999 TH/s"
        for index in range(6):
            result=parse_gpu_log(text,f"GPU-test-{index}",index)
            self.assertEqual(result["hashrate"],10+index)
            self.assertEqual(result["accepted"],100+index)
            self.assertEqual(result["rejected"],index)
            self.assertIsNone(result["average_1m"])
            self.assertEqual(result["log_gpu_index"],index)

    def test_gpu0_local_is_allowed_only_when_no_other_gpu_labels_exist(self):
        own=parse_gpu_log(line(0,90),"GPU-test-5",5,allow_local_gpu0=True)
        self.assertEqual(own["hashrate"],90)
        self.assertEqual(own["gpu_index"],5)
        self.assertEqual(own["log_gpu_index"],0)
        forbidden=parse_gpu_log(line(0,90),"GPU-test-5",5,allow_local_gpu0=False)
        self.assertIsNone(forbidden["hashrate"])
        mixed=parse_gpu_log(line(0,90)+"\n"+line(1,80),"GPU-test-5",5,allow_local_gpu0=True)
        self.assertIsNone(mixed["hashrate"])

    def test_shares_and_pause_are_scoped_to_selected_card(self):
        text="\n".join([line(0,10),line(1,20),
            "[2026-10-24 23:00:01] GPU0[t0] share accepted",
            "[2026-10-24 23:00:02] GPU1[t0] share accepted",
            "[2026-10-24 23:00:03] GPU0 turned off"])
        zero=parse_gpu_log(text,"GPU-test-0",0)
        one=parse_gpu_log(text,"GPU-test-1",1)
        self.assertTrue(zero["paused"])
        self.assertFalse(one["paused"])
        self.assertEqual(one["last_share_at"]-zero["last_share_at"],1)

    def test_late_log_line_keeps_newest_live_hash(self):
        now=dt.datetime(2026,10,24,23,0,10).timestamp()
        text=line(0,81,stamp="2026-10-24 23:00:09")+"\n"+line(0,80,stamp="2026-10-24 23:00:01")
        result=parse_gpu_log(text,"GPU-test-0",0,now)
        self.assertEqual(result["hashrate"],81)
        self.assertEqual(len(result["rate_points"]),2)
        self.assertEqual(result["rate_points"],sorted(result["rate_points"]))

    def test_no_own_log_does_not_reuse_another_gpu(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/"missing.log"
            result=read_device_payload(device(3),path,miner_running=False)
            self.assertIsNone(result["miner"]["hashrate"])
            self.assertIsNotNone(result["log_error"])
            self.assertFalse(result["miner_running"])

    def test_srbminer_index_can_differ_from_driver_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/"mining.log"
            path.write_text(line(2,77)+"\n"+line(1,12),encoding="utf-8")
            gpu={**device(1),"miner_index":2}
            result=read_device_payload(gpu,path)
            self.assertEqual(result["miner"]["hashrate"],77)
            self.assertEqual(result["miner"]["gpu_index"],1)
            self.assertEqual(result["miner"]["miner_index"],2)
            self.assertEqual(result["miner"]["log_gpu_index"],2)


if __name__=="__main__":
    unittest.main()
