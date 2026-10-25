import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from contribution import AUTHOR_WALLET,atomic_json
from rig import RigManager,write_request,parse_miner_device_map,REFERENCE_RTX3070_UUID
from rig_config import MAX_GPUS,gpu_paths,resolve_devices,update_gpu_settings

OTHER="prl1"+"q"*40


def detected(count=6,mixed=True):
    return [{"vendor":"NVIDIA","uuid":f"GPU-test-{index}","index":index,
        "name":("NVIDIA GeForce RTX 3070" if not mixed or index%2==0 else "NVIDIA GeForce GTX 1660 SUPER")}
        for index in range(count)]


class FakeBackend:
    def __init__(self):
        self.next_pid=100
        self.processes={}
        self.started=[]
        self.stopped=[]
        self.fail_uuid=None
        self.legacy=[]

    def start(self,arguments,working_dir,log_path=None):
        argv=list(map(str,arguments))
        if self.fail_uuid and self.fail_uuid in argv: raise RuntimeError("Failure isolated to selected GPU")
        self.next_pid+=1
        identity={"pid":self.next_pid,"created_at":str(self.next_pid*1000),"executable":argv[0],
            "argv":argv,"launch_id":str(self.next_pid)}
        self.processes[identity["pid"]]={"identity":identity,"alive":True}
        self.started.append(identity)
        return copy.deepcopy(identity)

    def inspect(self,pid,**kwargs):
        entry=self.processes.get(pid)
        return copy.deepcopy(entry["identity"]) if entry and entry["alive"] else None

    def is_owned(self,identity):
        current=self.inspect(identity.get("pid")) if isinstance(identity,dict) else None
        return bool(current and current==identity)

    def stop(self,identity):
        if not self.is_owned(identity): return False
        self.processes[identity["pid"]]["alive"]=False
        self.stopped.append(identity["pid"])
        # A controller exits normally when its own miner exits.
        for entry in self.processes.values():
            argv=entry["identity"]["argv"]
            if "--miner-pid" in argv and argv[argv.index("--miner-pid")+1]==str(identity["pid"]):
                entry["alive"]=False
        return True

    def wait(self,identity,timeout=12):
        return not self.is_owned(identity)

    def legacy_candidates(self,miner_path): return copy.deepcopy(self.legacy)

    def miner_device_map(self,miner_path,devices): return {gpu["uuid"]:gpu["index"] for gpu in devices}


class RigConfigurationTests(unittest.TestCase):
    def test_srb_gpu_ids_are_resolved_by_unique_pci_not_model_or_nvidia_index(self):
        devices=detected(2,mixed=False)
        devices[0]["pci_bus_id"]="00000000:06:00.0"
        devices[1]["pci_bus_id"]="00000000:0a:00.0"
        output="GPU3: nvidia_geforce_rtx_3070 [BUS: 06]\nGPU1: nvidia_geforce_rtx_3070 [BUS: 0A]"
        self.assertEqual(parse_miner_device_map(output,devices),{"GPU-test-0":3,"GPU-test-1":1})
        with self.assertRaises(RuntimeError): parse_miner_device_map("GPU0: NVIDIA unknown",devices)
        devices[1]["pci_bus_id"]="00000000:06:00.1"
        with self.assertRaises(RuntimeError): parse_miner_device_map(output,devices)
    def test_six_same_or_mixed_models_have_independent_uuid_paths_and_default_wallet(self):
        for mixed in (True,False):
            devices=resolve_devices(detected(mixed=mixed),{},Path("C:/rig-logs"))
            self.assertEqual(len(devices),6)
            self.assertTrue(all(gpu["enabled"] for gpu in devices))
            self.assertEqual(len({gpu["log_dir"] for gpu in devices}),6)
            self.assertTrue(all(gpu["wallet"]==AUTHOR_WALLET for gpu in devices))

    def test_global_wallet_and_per_gpu_override_follow_uuid_when_indices_reorder(self):
        settings={"wallet":OTHER,"gpu_settings":{"GPU-test-1":{"wallet":AUTHOR_WALLET,"enabled":False}}}
        devices=detected(2)
        devices[0]["index"],devices[1]["index"]=1,0
        resolved={gpu["uuid"]:gpu for gpu in resolve_devices(devices,settings,"C:/rig-logs")}
        self.assertEqual(resolved["GPU-test-0"]["wallet"],OTHER)
        self.assertEqual(resolved["GPU-test-1"]["wallet"],AUTHOR_WALLET)
        self.assertFalse(resolved["GPU-test-1"]["enabled"])
        self.assertFalse(resolved["GPU-test-1"]["uses_default_wallet"])

    def test_limit_six_and_reject_path_traversal_non_nvidia(self):
        self.assertEqual(len(resolve_devices(detected(8),{},"C:/logs")),MAX_GPUS)
        self.assertEqual(resolve_devices([{**detected(1)[0],"vendor":"AMD"}],{},"C:/logs"),[])
        with self.assertRaises(ValueError): gpu_paths("C:/logs","../../outside")

    def test_malformed_one_gpu_setting_disables_only_that_gpu(self):
        settings={"gpu_settings":{"GPU-test-1":{"enabled":"yes"}}}
        resolved=resolve_devices(detected(2),settings,"C:/logs")
        self.assertTrue(resolved[0]["enabled"])
        self.assertFalse(resolved[1]["enabled"])

    def test_update_returns_copy_and_validates_wallet(self):
        original={"wallet":OTHER}
        changed=update_gpu_settings(original,"GPU-test-1",enabled=False,wallet_override=AUTHOR_WALLET,change_wallet=True)
        self.assertNotIn("gpu_settings",original)
        self.assertEqual(changed["gpu_settings"]["GPU-test-1"]["wallet"],AUTHOR_WALLET)
        with self.assertRaises(ValueError): update_gpu_settings(original,"GPU-test-1",wallet_override="bad",change_wallet=True)


class RigManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.settings_path=self.root/"settings.json"
        atomic_json(self.settings_path,{"wallet":AUTHOR_WALLET})
        self.backend=FakeBackend()
        self.manager=self.make_manager()

    def make_manager(self):
        return RigManager(self.root/"state",self.root/"logs",self.settings_path,self.root/"SRBMiner-MULTI.exe",
            self.root/"python.exe",backend=self.backend,detector=lambda:detected(),clock=lambda:1000,
            proxy_ready=lambda entry,identity:43210)

    def tearDown(self): self.temp.cleanup()

    def test_start_six_independent_miners_controllers_scoped_index_uuid(self):
        self.manager.reconcile()
        self.assertEqual(len(self.backend.started),12)
        miners=[item for item in self.backend.started if "--gpu-id" in item["argv"]]
        self.assertEqual({item["argv"][item["argv"].index("--gpu-id")+1] for item in miners},set("012345"))
        self.assertTrue(all("--gpu-cclock0" not in item["argv"] for item in miners))
        for index,entry in enumerate(self.manager.devices.values()):
            self.assertEqual(entry["state"],"running")
            self.assertIn(entry["gpu_uuid"],entry["controller"]["argv"])
            self.assertIn("--miner-log",entry["controller"]["argv"])

    def test_disabling_one_gpu_never_stops_another(self):
        self.manager.reconcile()
        original={key:entry["miner_pid"] for key,entry in self.manager.devices.items()}
        settings={"wallet":AUTHOR_WALLET,"gpu_settings":{"GPU-test-2":{"enabled":False}}}
        self.manager.reconcile(settings=settings)
        self.assertEqual(self.backend.stopped,[original["GPU-test-2"]])
        for key,pid in original.items():
            if key!="GPU-test-2": self.assertTrue(self.backend.processes[pid]["alive"])

    def test_wallet_override_restarts_only_affected_gpu(self):
        self.manager.reconcile()
        original={key:entry["miner_pid"] for key,entry in self.manager.devices.items()}
        self.manager.reconcile(settings={"wallet":AUTHOR_WALLET,"gpu_settings":{"GPU-test-3":{"wallet":OTHER}}})
        self.assertEqual(self.backend.stopped,[original["GPU-test-3"]])
        self.assertEqual(self.manager.devices["GPU-test-3"]["wallet"],OTHER)
        self.assertNotEqual(self.manager.devices["GPU-test-3"]["miner_pid"],original["GPU-test-3"])

    def test_failure_one_controller_cleans_only_its_miner_and_other_gpus_run(self):
        self.backend.fail_uuid="GPU-test-3"
        self.manager.reconcile()
        self.assertEqual(self.manager.devices["GPU-test-3"]["state"],"error")
        self.assertTrue(all(entry["state"]=="running" for key,entry in self.manager.devices.items() if key!="GPU-test-3"))
        self.assertEqual(len(self.backend.stopped),1)

    def test_restart_preserves_valid_processes_without_duplication(self):
        self.manager.reconcile()
        restarted=self.make_manager()
        restarted.reconcile()
        self.assertEqual(len(self.backend.started),12)
        self.assertEqual(self.backend.stopped,[])

    def test_reused_pid_is_never_killed(self):
        self.manager.reconcile()
        original=self.manager.devices["GPU-test-1"]["miner_pid"]
        identity=self.backend.processes[original]["identity"]
        self.backend.processes[original]["identity"]={**identity,"created_at":"9999999","argv":["unrelated.exe"],"launch_id":"different"}
        restarted=self.make_manager()
        restarted.reconcile(settings={"wallet":AUTHOR_WALLET,"gpu_settings":{"GPU-test-1":{"enabled":False}}})
        self.assertNotIn(original,self.backend.stopped)
        self.assertTrue(self.backend.processes[original]["alive"])

    def test_alive_same_identity_but_unverified_commandline_blocks_duplicate_and_stop(self):
        self.manager.reconcile()
        entry=self.manager.devices["GPU-test-1"]
        original=entry["miner_pid"]
        self.backend.processes[original]["identity"]["argv"].append("unexpected")
        count=len(self.backend.started)
        self.manager.reconcile()
        self.assertEqual(self.manager.devices["GPU-test-1"]["state"],"ownership_unverified")
        self.assertEqual(len(self.backend.started),count)
        self.assertNotIn(original,self.backend.stopped)

    def test_corrupt_registry_fails_closed_without_any_process_action(self):
        self.manager.registry_path.parent.mkdir(parents=True,exist_ok=True)
        self.manager.registry_path.write_text('{broken')
        restarted=self.make_manager()
        with self.assertRaises(RuntimeError): restarted.reconcile()
        self.assertEqual(self.backend.started,[])
        self.assertEqual(self.backend.stopped,[])

    def test_request_queue_stops_only_selected_and_persists_choice(self):
        self.manager.reconcile()
        original=self.manager.devices["GPU-test-4"]["miner_pid"]
        request=write_request(self.manager.state_dir,"stop","GPU-test-4")
        # Manager clock is deterministic for process/retry tests; request timestamp must match it.
        path=self.manager.state_dir/"rig-requests"/(request["id"]+".json")
        request["at"]=1000;atomic_json(path,request)
        self.manager.process_requests()
        self.manager.reconcile()
        saved=json.loads(self.settings_path.read_text(encoding="utf-8-sig"))
        self.assertFalse(saved["gpu_settings"]["GPU-test-4"]["enabled"])
        self.assertEqual(self.backend.stopped,[original])
        self.assertTrue((path.parent/"processed"/path.name).exists())

    def test_stop_then_start_requests_follow_timestamps_not_reversed_random_filenames(self):
        self.manager.reconcile()
        identifier="GPU-test-2"
        original=self.manager.devices[identifier]["miner_pid"]
        queue_dir=self.manager.state_dir/"rig-requests"
        atomic_json(queue_dir/"zzzz-stop.json",{"id":"stop-first","action":"stop","gpu_uuid":identifier,"at":990})
        atomic_json(queue_dir/"aaaa-start.json",{"id":"start-second","action":"start","gpu_uuid":identifier,"at":991})
        self.manager.process_requests()
        settings=json.loads(self.settings_path.read_text(encoding="utf-8-sig"))
        self.assertTrue(settings["gpu_settings"][identifier]["enabled"])
        self.assertEqual(self.manager.processed_ids[-2:],["stop-first","start-second"])
        self.manager.reconcile()
        self.assertEqual(self.manager.devices[identifier]["state"],"running")
        self.assertNotEqual(self.manager.devices[identifier]["miner_pid"],original)
        self.assertEqual(self.backend.stopped,[original])

    def test_support_uses_effective_wallet_and_hot_disable_does_not_restart_gpu(self):
        settings={"wallet":OTHER,"support_enabled":True,"gpu_settings":{"GPU-test-1":{"wallet":AUTHOR_WALLET}}}
        self.manager.reconcile(detected=detected(2),settings=settings)
        first,second=self.manager.devices.values()
        self.assertIsNotNone(first["proxy_pid"])
        self.assertIsNone(second["proxy_pid"])
        miners=[first["miner_pid"],second["miner_pid"]]
        settings["support_enabled"]=False
        self.manager.reconcile(detected=detected(2),settings=settings)
        self.assertEqual([entry["miner_pid"] for entry in self.manager.devices.values()],miners)
        self.assertEqual(self.backend.stopped,[])
        proxy_settings=json.loads((Path(first["log_dir"])/"proxy-settings.json").read_text())
        self.assertFalse(proxy_settings["support_enabled"])

    def test_legacy_gpu0_is_adopted_read_only_and_never_stopped(self):
        miner=self.backend.start([self.manager.miner_path,"--gpu-id","0","--wallet",AUTHOR_WALLET,
            "--log-file",self.root/"legacy.log"],self.root)
        controller=self.backend.start([self.root/"python.exe",self.manager.log_dir.parent/"thermal_controller.py",
            "--miner-pid",str(miner["pid"])],self.root)
        self.backend.legacy=[miner,controller]
        self.assertTrue(self.manager.adopt_legacy())
        self.manager.reconcile()
        entry=self.manager.devices["GPU-test-0"]
        self.assertEqual(entry["miner_pid"],miner["pid"])
        self.assertTrue(entry["external_readonly"])
        self.assertEqual(entry["controller_pid"],controller["pid"])
        self.assertFalse(self.manager.stop_gpu("GPU-test-0"))
        self.assertEqual(self.backend.stopped,[])
        self.assertEqual(len(self.backend.started),12) # legacy pair + five managed pairs

    def test_driver_reorder_restarts_changed_indices_and_keeps_wallet_by_uuid(self):
        settings={"wallet":AUTHOR_WALLET,"gpu_settings":{"GPU-test-1":{"wallet":OTHER}}}
        self.manager.reconcile(detected=detected(2),settings=settings)
        old={key:entry["miner_pid"] for key,entry in self.manager.devices.items()}
        devices=detected(2)
        devices[0]["index"],devices[1]["index"]=1,0
        self.manager.reconcile(detected=devices,settings=settings)
        self.assertEqual(set(self.backend.stopped),set(old.values()))
        self.assertEqual(self.manager.devices["GPU-test-1"]["wallet"],OTHER)
        self.assertEqual(self.manager.devices["GPU-test-1"]["index"],0)

    def test_dead_proxy_stops_only_affected_worker_and_issues_graceful_controller_stop(self):
        self.manager.reconcile(detected=detected(2),settings={"wallet":OTHER,"support_enabled":True})
        entry=self.manager.devices["GPU-test-1"]
        miner=entry["miner_pid"]
        self.backend.processes[entry["proxy_pid"]]["alive"]=False
        self.manager.reconcile(detected=detected(2),settings={"wallet":OTHER,"support_enabled":True})
        self.assertEqual(self.backend.stopped,[miner])
        stop=json.loads((Path(entry["log_dir"])/"gpu-agent-stop.json").read_text())
        self.assertEqual(stop["gpu_uuid"],"GPU-test-1")
        self.assertTrue(self.backend.processes[self.manager.devices["GPU-test-0"]["miner_pid"]]["alive"])

    def test_replacing_removed_devices_keeps_registry_at_six_and_archives_old_metadata(self):
        self.manager.reconcile()
        replacement=detected()
        replacement[-1]["uuid"]="GPU-replacement-5"
        self.manager.reconcile(detected=replacement)
        self.assertEqual(len(self.manager.devices),6)
        self.assertNotIn("GPU-test-5",self.manager.devices)
        self.assertEqual(self.manager.devices["GPU-replacement-5"]["state"],"running")
        self.assertTrue((self.manager.state_dir/"rig-device-history.jsonl").exists())
        restarted=self.make_manager()
        self.assertTrue(restarted.registry_valid)

    def prepare_legacy_migration(self):
        gpu={**detected(1)[0],"uuid":REFERENCE_RTX3070_UUID,"power_default_w":240,"supported_graphics_clocks_mhz":[1500,1740,1755]}
        self.manager.detector=lambda:[gpu]
        miner=self.backend.start([self.manager.miner_path,"--gpu-id","0","--wallet",AUTHOR_WALLET,
            "--log-file",self.root/"legacy.log"],self.root)
        controller=self.backend.start([self.root/"python.exe",self.manager.log_dir.parent/"thermal_controller.py",
            "--miner-pid",str(miner["pid"])],self.root)
        supervisor=self.backend.start([self.root/"powershell.exe","-File",self.manager.log_dir.parent/"Iniciar-Pearl.ps1"],self.root)
        self.backend.legacy=[miner,controller,supervisor]
        return gpu,miner,controller,supervisor

    def test_explicit_migration_waits_old_supervisor_cleanup_before_new_reference_worker(self):
        gpu,miner,controller,supervisor=self.prepare_legacy_migration()
        waited=[]
        original_wait=self.backend.wait
        def wait(identity,timeout=12):
            waited.append(identity["pid"])
            if identity["pid"]==supervisor["pid"]:
                self.backend.processes[identity["pid"]]["alive"]=False
                return True
            return original_wait(identity,timeout)
        self.backend.wait=wait
        self.manager.migrate_legacy(gpu["uuid"],reference_rtx3070=True)
        self.assertEqual(waited,[controller["pid"],supervisor["pid"]])
        self.assertEqual(self.backend.stopped,[miner["pid"]])
        self.assertEqual(len(self.backend.started),3)
        self.manager.reconcile()
        entry=self.manager.devices[gpu["uuid"]]
        args=entry["miner"]["argv"]
        self.assertEqual(args[args.index("--gpu-cclock0")+1],"1740")
        self.assertEqual(args[args.index("--gpu-fan0")+1],"100")
        self.assertFalse(entry["external_readonly"])

    def test_migration_wrong_uuid_or_changed_identity_never_stops_any_process(self):
        gpu,miner,controller,supervisor=self.prepare_legacy_migration()
        with self.assertRaises(RuntimeError):
            self.manager.migrate_legacy("GPU-another-board",reference_rtx3070=True)
        self.manager.adopt_legacy()
        self.backend.processes[controller["pid"]]["identity"]["created_at"]="9999999"
        with self.assertRaises(RuntimeError):
            self.manager.migrate_legacy(gpu["uuid"],reference_rtx3070=True)
        self.assertEqual(self.backend.stopped,[])

    def test_migration_does_not_start_new_worker_before_legacy_supervisor_finishes(self):
        gpu,miner,controller,supervisor=self.prepare_legacy_migration()
        with self.assertRaises(RuntimeError):
            self.manager.migrate_legacy(gpu["uuid"],reference_rtx3070=True)
        self.assertEqual(self.manager.devices[gpu["uuid"]]["state"],"migration_failed")
        self.assertEqual(len(self.backend.started),3)
        self.assertTrue(self.backend.processes[supervisor["pid"]]["alive"])

    def test_reference_identity_persists_and_recovery_reuses_1740_only_after_fresh_checks(self):
        gpu,miner,controller,supervisor=self.prepare_legacy_migration()
        def completed(identity,timeout=12):
            self.backend.processes[identity["pid"]]["alive"]=False
            return True
        self.backend.wait=completed
        self.manager.migrate_legacy(gpu["uuid"],reference_rtx3070=True)
        self.manager.reconcile()
        self.manager.stop_gpu(gpu["uuid"])
        self.manager.publish()
        recovered=self.make_manager()
        recovered.detector=lambda:[gpu]
        self.assertEqual(recovered.reference_uuid,REFERENCE_RTX3070_UUID)
        recovered.reconcile()
        args=recovered.devices[gpu["uuid"]]["miner"]["argv"]
        self.assertEqual(args[args.index("--gpu-cclock0")+1],"1740")
        self.assertEqual(args[args.index("--gpu-plimit0")+1],"240")

    def test_reference_profile_unknown_current_limits_never_starts_or_falls_back_to_stock(self):
        gpu,miner,controller,supervisor=self.prepare_legacy_migration()
        def completed(identity,timeout=12):
            self.backend.processes[identity["pid"]]["alive"]=False
            return True
        self.backend.wait=completed
        self.manager.migrate_legacy(gpu["uuid"],reference_rtx3070=True)
        self.manager.detector=lambda:[{**gpu,"power_default_w":None}]
        previous=len(self.backend.started)
        self.manager.reconcile()
        self.assertEqual(len(self.backend.started),previous)
        self.assertEqual(self.manager.devices[gpu["uuid"]]["state"],"error")
        self.assertIn("não confirmados",self.manager.devices[gpu["uuid"]]["error"])

    def test_cli_request_needs_only_state_dir_and_does_not_start_processes(self):
        import rig
        with patch("sys.argv",["rig.py","--request","reconcile","--state-dir",str(self.manager.state_dir)]), \
             patch("builtins.print") as output:
            rig.main()
        self.assertEqual(self.backend.started,[])
        self.assertEqual(len(list((self.manager.state_dir/"rig-requests").glob("*.json"))),1)
        self.assertEqual(json.loads(output.call_args[0][0])["action"],"reconcile")


if __name__=="__main__": unittest.main()
