"""Tests exercise limits, independent measurements, restart durability and ownership."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from gpu_agent import (GPUAgent, NvidiaActuator, OllamaAdvisor, detect_nvidia_gpus,
                       thermal_limits, validated_plan, control, BackgroundAdvisor,
                       select_device,read_miner_data,external_supervisor_active)
from gpu_agent import process_identity,same_process,requested_control_stop


def device(identifier="GPU-test-3070"):
    return {"vendor":"NVIDIA","uuid":identifier,"name":"NVIDIA GeForce RTX 3070","index":0,
            "power_min_w":100,"power_max_w":250,"power_default_w":220,"power_limit_w":180,
            "supported_graphics_clocks_mhz":[1500,1515,1530,1545,1560],"temperature_limit_c":88}


def sample(stamp=1000,temp=68,clock=1530,power=180):
    return {"temperature_c":temp,"power_w":150,"power_limit_w":power,
            "clock_mhz":clock,"utilization_pct":100,"sampled_at":stamp}


class GPUAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.agent=GPUAgent(self.temp.name)
        self.gpu=device()

    def tearDown(self):
        self.agent.close()
        self.temp.cleanup()

    def completed(self):
        self.agent.start_benchmark(self.gpu,300,now=1000)
        for elapsed in range(0,301,10):
            stamp=1000+elapsed
            hash_stamp=1000+(elapsed//60)*60
            self.agent.observe(self.gpu,sample(stamp),80,10,0,now=stamp,hash_sampled_at=hash_stamp)
        return self.agent.status(self.gpu["uuid"],now=1300)

    def test_only_driver_identified_nvidia_can_enter_engine(self):
        for gpu in ({**self.gpu,"vendor":"AMD"},{**self.gpu,"uuid":"0; rm -rf"}):
            with self.assertRaises(ValueError):
                self.agent.start_benchmark(gpu)

    def test_complete_benchmark_requires_real_coverage_and_independent_hashes(self):
        result=self.completed()
        self.assertTrue(result["validated"])
        self.assertEqual(result["observed_seconds"],300)
        self.assertEqual(result["independent_hash_samples"],5)
        self.assertAlmostEqual(result["mean_hashrate_ths"],80)
        self.assertEqual(result["best_profile"]["samples"],5)

    def test_restart_does_not_invent_observations_during_gap(self):
        self.agent.start_benchmark(self.gpu,300,now=1000)
        self.agent.observe(self.gpu,sample(1000),80,10,0,now=1000,hash_sampled_at=1000)
        self.agent.close()
        self.agent=GPUAgent(self.temp.name)
        for stamp in (1360,1370,1380):
            self.agent.observe(self.gpu,sample(stamp),80,10,0,now=stamp,hash_sampled_at=stamp)
        result=self.agent.status(self.gpu["uuid"],now=1380)
        self.assertEqual(result["state"],"complete")
        self.assertFalse(result["validated"])
        self.assertLess(result["observed_seconds"],30)

    def test_repeated_hash_log_is_not_independent_measurement(self):
        self.agent.start_benchmark(self.gpu,60,now=1000)
        for stamp in range(1030,1061,2):
            self.agent.observe(self.gpu,sample(stamp),80,10,0,now=stamp,hash_sampled_at=1030)
        result=self.agent.status(self.gpu["uuid"],now=1060)
        self.assertEqual(result["independent_hash_samples"],1)
        self.assertFalse(result["validated"])

    def test_history_per_gpu_and_manual_rebenchmark_preserve_old_session(self):
        old=self.completed()["id"]
        self.agent.start_benchmark(self.gpu,300,now=1400)
        self.assertNotEqual(self.agent.status(self.gpu["uuid"])["id"],old)
        self.assertEqual(self.agent.db.execute("SELECT COUNT(*) FROM observations WHERE session=?",(old,)).fetchone()[0],31)
        self.assertEqual(self.agent.status("GPU-another")["state"],"idle")

    def test_rejected_share_blocks_completed_validation(self):
        self.agent.start_benchmark(self.gpu,60,now=1000)
        for elapsed in range(0,61,10):
            self.agent.observe(self.gpu,sample(1000+elapsed),80,10,int(elapsed>=40),now=1000+elapsed,hash_sampled_at=1000+elapsed)
        result=self.agent.status(self.gpu["uuid"],now=1060)
        self.assertFalse(result["validated"])
        self.assertGreater(result["unstable_samples"],0)

    def test_thermal_emergency_overrides_llm_and_requests_supervisor_pause(self):
        class UnsafeAdvisor:
            def advise(self,context):
                raise AssertionError("Thermal emergency must bypass LLM")
        self.agent.advisor=UnsafeAdvisor()
        result=self.agent.recommend(self.gpu,sample(1000,temp=84),ollama=True,now=1000)
        self.assertTrue(result["pause_required"])
        self.assertEqual(result["action"],"clock_down")
        self.assertEqual(result["clock_mhz"],1515)

    def test_unknown_capabilities_do_not_authorize_writes(self):
        gpu={**self.gpu,"supported_graphics_clocks_mhz":[],"power_min_w":None}
        self.assertEqual(validated_plan(gpu,sample(),"clock_up")["action"],"hold")
        self.assertEqual(validated_plan(gpu,sample(),"power_down")["action"],"hold")

    def test_steps_are_small_driver_supported_and_power_never_above_default(self):
        self.assertEqual(validated_plan(self.gpu,sample(),"clock_up")["clock_mhz"],1545)
        self.assertEqual(validated_plan(self.gpu,sample(),"power_up")["power_limit_w"],185)
        self.assertEqual(validated_plan(self.gpu,sample(power=220),"power_up")["action"],"hold")
        self.assertEqual(validated_plan(self.gpu,sample(clock=1560),"clock_up")["action"],"hold")

    def test_cooldown_is_since_last_action_not_last_poll(self):
        self.agent.start_benchmark(self.gpu,300,now=1000)
        for stamp in range(1000,1081,10):
            self.agent.observe(self.gpu,sample(stamp),80,10,0,now=stamp,hash_sampled_at=stamp)
        self.assertEqual(self.agent.recommend(self.gpu,sample(1080),now=1080)["action"],"clock_up")
        self.assertEqual(self.agent.recommend(self.gpu,sample(1082),now=1082)["action"],"hold")
        self.assertEqual(self.agent.recommend(self.gpu,sample(1111),now=1111)["action"],"clock_up")

    def test_trial_loss_and_rejections_return_bounded_rollback(self):
        plan=validated_plan(self.gpu,sample(),"clock_up")
        plan.update(gpu_uuid=self.gpu["uuid"],at=1000)
        self.agent.record_application(self.gpu,plan,sample(),80,now=1000)
        self.agent.trial_feedback(self.gpu,sample(1060,clock=1545),70,1060,now=1060)
        result=self.agent.trial_feedback(self.gpu,sample(1120,clock=1545),70,1120,now=1120)
        self.assertEqual(result["state"],"rejected")
        self.assertEqual(result["rollback"]["clock_mhz"],1530)

    def test_trial_rollback_executes_through_revalidating_actuator(self):
        now=time.time()
        plan=validated_plan(self.gpu,sample(now),"clock_up")
        plan.update(gpu_uuid=self.gpu["uuid"],at=now)
        self.agent.record_application(self.gpu,plan,sample(now),80,now=now-100)
        current=sample(now,clock=1545)
        result=self.agent.trial_feedback(self.gpu,current,60,now,new_rejected_shares=1,now=now)
        commands=[]
        actuator=NvidiaActuator(self.temp.name,enabled=True,command=commands.append)
        actuator.acquire(self.gpu["uuid"])
        try:
            actuator.apply(self.gpu,current,result["rollback"])
            self.assertEqual(commands,[["-i",self.gpu["uuid"],"-lgc","1530,1530"]])
        finally:
            actuator.close()

    def test_manual_trial_cancel_preserves_audit_and_rolls_back(self):
        plan={"action":"power_down","gpu_uuid":self.gpu["uuid"]}
        self.agent.record_application(self.gpu,plan,sample(),80,now=1000)
        rollback=self.agent.cancel_trial(self.gpu,sample(1090,power=175),now=1090)
        self.assertEqual(rollback["power_limit_w"],180)
        self.assertEqual(self.agent.db.execute("SELECT state FROM trials").fetchone()[0],"cancelled_by_user")

    def test_trial_survives_restart_without_duplicate_hashes(self):
        plan={"action":"clock_up","gpu_uuid":self.gpu["uuid"]}
        self.agent.record_application(self.gpu,plan,sample(),80,now=1000)
        self.agent.trial_feedback(self.gpu,sample(1060),81,1060,now=1060)
        self.agent.close()
        self.agent=GPUAgent(self.temp.name)
        repeated=self.agent.trial_feedback(self.gpu,sample(1090),81,1060,now=1090)
        self.assertEqual(repeated["independent_hash_samples"],1)
        result=self.agent.trial_feedback(self.gpu,sample(1120),82,1120,now=1120)
        self.assertEqual(result["state"],"accepted")

    def test_export_requires_validated_session_and_expires(self):
        self.assertIsNone(self.agent.export_policy(self.gpu,self.temp.name,now=1300))
        self.completed()
        result=self.agent.export_policy(self.gpu,self.temp.name,now=1300)
        self.assertEqual(result["valid_until"],4900)
        self.assertFalse(result["authorization"])


class OllamaAndActuatorTests(unittest.TestCase):
    def test_background_inference_never_blocks_sensor_loop_and_is_rate_limited(self):
        started=threading.Event()
        release=threading.Event()
        calls=[]
        class SlowAdvisor:
            def advise(self,context):
                calls.append(context)
                started.set()
                release.wait(1)
                return {"action":"hold","reason":"Validar mais amostras"}
        advisor=BackgroundAdvisor(SlowAdvisor())
        before=time.monotonic()
        pending=advisor.poll({"temperature":70})
        self.assertLess(time.monotonic()-before,.2)
        self.assertIsNone(pending["advice"])
        self.assertTrue(started.wait(.5))
        advisor.poll({"temperature":71})
        self.assertEqual(len(calls),1)
        release.set()
        advisor.thread.join(1)
        cached=advisor.poll({"temperature":72})
        self.assertEqual(cached["advice"]["action"],"hold")
        self.assertEqual(cached["status"],"local_ollama")
        self.assertEqual(len(calls),1)

    def test_background_failure_reuses_adaptive_fallback_without_request_spam(self):
        class MissingAdvisor:
            calls=0
            def advise(self,context):
                self.calls+=1
                raise ValueError("Ollama indisponível")
        source=MissingAdvisor()
        advisor=BackgroundAdvisor(source)
        advisor.poll({})
        advisor.thread.join(1)
        for _ in range(5):
            result=advisor.poll({})
        self.assertIsNone(result["advice"])
        self.assertIn("unavailable",result["status"])
        self.assertEqual(source.calls,1)

    def test_llm_cannot_supply_commands_numbers_or_remote_endpoint(self):
        for text in ('{"action":"run","reason":"x"}', '{"action":"clock_up","reason":"x","clock":9999}', 'not json'):
            with self.assertRaises((ValueError,json.JSONDecodeError)):
                OllamaAdvisor.parse(text)
        with self.assertRaises(ValueError):
            OllamaAdvisor(endpoint="https://api.example.com")

    def test_local_request_is_cpu_only_bounded_and_validated(self):
        captured={}
        class Response:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,limit):
                return json.dumps({"response":json.dumps({"action":"hold","reason":"Sensores estáveis"})}).encode()
        class Opener:
            def open(self,request,timeout):
                captured.update(payload=json.loads(request.data),timeout=timeout,url=request.full_url)
                return Response()
        with patch("urllib.request.build_opener",return_value=Opener()):
            result=OllamaAdvisor(timeout=90).advise({"sample":{}})
        self.assertEqual(result["action"],"hold")
        self.assertEqual(captured["payload"]["options"]["num_gpu"],0)
        self.assertEqual(captured["timeout"],30)

    def test_actuator_defaults_read_only_and_exclusive_lease_is_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            actuator=NvidiaActuator(temporary)
            with self.assertRaises(RuntimeError):
                actuator.acquire("GPU-test")
            actuator=NvidiaActuator(temporary,enabled=True)
            with self.assertRaises(RuntimeError):
                actuator.acquire("GPU-test",external_controller_active=True)
            actuator.acquire("GPU-test")
            other=NvidiaActuator(temporary,enabled=True)
            with self.assertRaises(RuntimeError):
                other.acquire("GPU-test")
            actuator.close()

    def test_actuator_revalidates_tampered_plan_and_external_controller(self):
        with tempfile.TemporaryDirectory() as temporary:
            commands=[]
            actuator=NvidiaActuator(temporary,enabled=True,command=commands.append)
            gpu=device()
            actuator.acquire(gpu["uuid"])
            now=time.time()
            current=sample(now)
            plan=validated_plan(gpu,current,"clock_up")
            plan.update(gpu_uuid=gpu["uuid"],at=now)
            with self.assertRaises(RuntimeError):
                actuator.apply(gpu,current,plan,external_controller_active=True)
            with self.assertRaises(ValueError):
                actuator.apply(gpu,current,{**plan,"clock_mhz":9999})
            self.assertEqual(commands,[])
            actuator.apply(gpu,current,plan)
            self.assertEqual(commands,[["-i",gpu["uuid"],"-lgc","1545,1545"]])
            actuator.close()

    def test_detection_gracefully_handles_unsupported_sensor_fields(self):
        def command(arguments):
            joined=" ".join(arguments)
            if "index,name,uuid,pci.bus_id" in joined:
                return "0, NVIDIA GeForce GTX 1060, GPU-test, 0000:06:00.0"
            if "supported-clocks" in joined:
                return "N/A"
            raise RuntimeError("Not Supported")
        result=detect_nvidia_gpus(command)
        self.assertEqual(result[0]["name"],"NVIDIA GeForce GTX 1060")
        self.assertIsNone(result[0]["power_min_w"])
        self.assertEqual(result[0]["supported_graphics_clocks_mhz"],[])

    def test_full_identity_preserves_optional_serial_pci_vbios_and_catalog_architecture(self):
        values={"driver_version":"580.95","serial":"[N/A]","pci.device_id":"0x248410DE",
                "pci.sub_device_id":"0x12341043","vbios_version":"94.04.46.00.01","compute_cap":"8.6"}
        def command(arguments):
            if arguments[0].startswith("--query-gpu=index,name"):
                return "0, NVIDIA GeForce RTX 3070, GPU-test-3070, 00000000:01:00.0"
            query=next((part.removeprefix("--query-gpu=") for part in arguments if part.startswith("--query-gpu=")),None)
            if query in values:
                return values[query]
            raise RuntimeError("Not supported")
        result=detect_nvidia_gpus(command)[0]
        self.assertIsNone(result["serial"])
        self.assertEqual(result["pci_device_id"],"0x248410DE")
        self.assertEqual(result["vbios_version"],"94.04.46.00.01")
        self.assertEqual(result["architecture"],"Ampere")
        self.assertEqual(result["catalog_match"],"name_and_pci")

    def test_uuid_selection_rejects_index_mismatch_instead_of_controlling_other_card(self):
        devices=[{**device(f"GPU-test-{index}"),"index":index} for index in range(6)]
        self.assertEqual(select_device(devices,SimpleNamespace(uuid="GPU-test-5",gpu=5))["index"],5)
        with self.assertRaises(RuntimeError):
            select_device(devices,SimpleNamespace(uuid="GPU-test-5",gpu=0))

    def test_rig_snapshot_extracts_only_selected_gpu_and_never_aggregate_hashrate(self):
        with tempfile.TemporaryDirectory() as temporary:
            gpu={**device("GPU-test-5"),"index":5}
            payload={"collected_at":1000,"miner":{"hashrate":999},"rig":{"devices":[
                {"identity":{"uuid":f"GPU-test-{index}"},"miner":{"hashrate":index+10,"hashrate_at":999}}
                for index in range(6)]}}
            path=Path(temporary)/"latest.json"
            path.write_text(json.dumps(payload),encoding="utf-8")
            miner,fresh=read_miner_data(SimpleNamespace(),gpu,temporary,6,now=1001)
            self.assertEqual(miner["hashrate"],15)
            self.assertTrue(fresh)
            del payload["rig"]
            path.write_text(json.dumps(payload),encoding="utf-8")
            miner,fresh=read_miner_data(SimpleNamespace(),gpu,temporary,6,now=1001)
            self.assertEqual(miner,{})
            self.assertFalse(fresh)
            payload["rig"]={"devices":{"GPU-other":{"miner":{"hashrate":555}}}}
            path.write_text(json.dumps(payload),encoding="utf-8")
            single_gpu={**gpu,"index":0}
            miner,fresh=read_miner_data(SimpleNamespace(),single_gpu,temporary,1,now=1001)
            self.assertEqual(miner,{})
            self.assertFalse(fresh)

    def test_other_gpu_supervisor_does_not_block_selected_gpu(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/"gpu-agent-control.json"
            path.write_text(json.dumps({"enabled":True,"controller_pid":123456,"gpu_uuid":"GPU-test-0"}))
            with patch("gpu_agent.process_alive",return_value=True):
                self.assertFalse(external_supervisor_active(temporary,gpu_uuid="GPU-test-5",gpu_index=5))
                self.assertTrue(external_supervisor_active(temporary,gpu_uuid="GPU-test-0",gpu_index=0))

    def test_legacy_thermal_supervisor_without_uuid_belongs_only_to_gpu_zero(self):
        with tempfile.TemporaryDirectory() as temporary:
            (Path(temporary)/"thermal-control.json").write_text(json.dumps({"enabled":True,"controller_pid":123456}))
            with patch("gpu_agent.process_alive",return_value=True):
                self.assertFalse(external_supervisor_active(temporary,gpu_uuid="GPU-test-5",gpu_index=5))
                self.assertTrue(external_supervisor_active(temporary,gpu_uuid="GPU-test-0",gpu_index=0))

    def test_control_baseline_does_not_tune_and_cleanup_restores_driver(self):
        with tempfile.TemporaryDirectory() as temporary:
            gpu=device()
            commands=[]
            args=SimpleNamespace(duration=300,gpu=0,miner_pid=123,ollama=False,objective="performance",samples=1)
            with patch("gpu_agent.detect_nvidia_gpus",return_value=[gpu]), \
                 patch("gpu_agent.process_identity",return_value={"created_filetime":1,"executable":"SRBMiner-MULTI.exe"}), \
                 patch("gpu_agent.process_alive",return_value=True), \
                 patch("gpu_agent.external_supervisor_active",return_value=False), \
                 patch("gpu_agent.read_sample",side_effect=lambda gpu:sample(time.time())), \
                 patch("gpu_agent.smi_command",side_effect=commands.append), \
                 patch("ctypes.WinDLL",return_value=SimpleNamespace(IsUserAnAdmin=lambda:True)):
                self.assertEqual(control(args,temporary,temporary),0)
            self.assertEqual(commands[0],["-i",gpu["uuid"],"-rgc"])
            self.assertEqual(commands[1][:3],["-i",gpu["uuid"],"-pl"])
            self.assertEqual(float(commands[1][3]),180)
            status=json.loads((Path(temporary)/"gpu-agent-control.json").read_text())
            self.assertFalse(status["enabled"])
            self.assertEqual(status["writes"],0)
            self.assertFalse((Path(temporary)/"gpu-control-owner.json").exists())

    def test_control_refuses_existing_thermal_supervisor_before_any_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            args=SimpleNamespace(duration=300,gpu=0,miner_pid=123,ollama=False,objective="performance",samples=1)
            with patch("gpu_agent.detect_nvidia_gpus",return_value=[device()]), \
                 patch("gpu_agent.external_supervisor_active",return_value=True), \
                 patch("gpu_agent.smi_command") as command, \
                 patch("ctypes.WinDLL",return_value=SimpleNamespace(IsUserAnAdmin=lambda:True)):
                with self.assertRaises(RuntimeError):
                    control(args,temporary,temporary)
                command.assert_not_called()

    def test_control_restarts_incomplete_baseline_without_tuning(self):
        with tempfile.TemporaryDirectory() as temporary:
            calls=[]
            class IncompleteAgent:
                advisor=None
                def start_benchmark(self,*args): calls.append(args)
                def observe(self,*args,**kwargs): return {"state":"complete","validated":False}
                def close(self): pass
            args=SimpleNamespace(duration=300,gpu=0,miner_pid=123,ollama=False,objective="performance",samples=1)
            with patch("gpu_agent.detect_nvidia_gpus",return_value=[device()]), \
                 patch("gpu_agent.process_identity",return_value={"created_filetime":1,"executable":"SRBMiner-MULTI.exe"}), \
                 patch("gpu_agent.process_alive",return_value=True), \
                 patch("gpu_agent.external_supervisor_active",return_value=False), \
                 patch("gpu_agent.read_sample",side_effect=lambda gpu:sample(time.time())), \
                 patch("gpu_agent.smi_command") as command, \
                 patch("gpu_agent.GPUAgent",return_value=IncompleteAgent()), \
                 patch("ctypes.WinDLL",return_value=SimpleNamespace(IsUserAnAdmin=lambda:True)):
                self.assertEqual(control(args,temporary,temporary),0)
            self.assertEqual(len(calls),2)
            status=json.loads((Path(temporary)/"gpu-agent-control.json").read_text())
            self.assertEqual(status["mode"],"insufficient_data")
            self.assertEqual(status["writes"],0)

    def test_pid_reuse_does_not_keep_supervisor_active(self):
        identity={"created_filetime":100,"executable":"SRBMiner-MULTI.exe"}
        replacement={"created_filetime":200,"executable":"SRBMiner-MULTI.exe"}
        with patch("gpu_agent.process_alive",return_value=True),patch("gpu_agent.process_identity",return_value=replacement):
            self.assertFalse(same_process(123,identity))

    def test_stop_request_requires_recent_current_pid_and_matching_uuid(self):
        import os
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/"gpu-agent-stop.json"
            payload={"gpu_uuid":"GPU-test-5","pid":os.getpid(),"at":1001}
            path.write_text(json.dumps(payload))
            self.assertTrue(requested_control_stop(temporary,"GPU-test-5",1000,now=1002))
            self.assertFalse(requested_control_stop(temporary,"GPU-test-0",1000,now=1002))
            self.assertFalse(requested_control_stop(temporary,"GPU-test-5",1000,now=1100))
            self.assertFalse(requested_control_stop(temporary,"GPU-test-5",1002,now=1003))
            payload["pid"]=os.getpid()+1
            path.write_text(json.dumps(payload))
            self.assertFalse(requested_control_stop(temporary,"GPU-test-5",1000,now=1002))


if __name__=="__main__":
    unittest.main()
