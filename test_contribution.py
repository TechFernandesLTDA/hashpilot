import json
from pathlib import Path
import queue
import socket
import tempfile
import threading
import time
import unittest

from contribution import (AUTHOR_WALLET, ContributionLedger, LocalStratumProxy,
    read_support_status, rewrite_message, support_configuration, ProxyLease)

USER="prl1"+"q"*40


def wait_for(predicate,timeout=3):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if predicate(): return
        time.sleep(.01)
    raise AssertionError("Timed out waiting for local test state")


class ContributionTests(unittest.TestCase):
    def test_proxy_instance_lease_refuses_concurrent_owner_and_recovers_stale_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/"owner.json"
            first=ProxyLease(path)
            try:
                with self.assertRaises(RuntimeError):
                    ProxyLease(path)
            finally:
                first.close()
            # An orphan's JSON is not a permanent lock: the OS owns exclusivity.
            path.write_text(json.dumps({"pid":999999,"active":True}))
            second=ProxyLease(path)
            second.close()
            self.assertFalse(json.loads(path.read_text())["active"])

    def test_opt_in_is_strict_false_by_default_and_author_never_pays_extra(self):
        for settings in ({},{"support_enabled":False},{"support_enabled":"true"},{"support_enabled":1},None,[]):
            self.assertFalse(support_configuration(settings,USER)["enabled"])
        self.assertTrue(support_configuration({"support_enabled":True},USER)["enabled"])
        self.assertFalse(support_configuration({"support_enabled":True},AUTHOR_WALLET)["enabled"])
        self.assertFalse(support_configuration({"support_enabled":True,"wallet":AUTHOR_WALLET},USER)["enabled"])

    def test_57_primary_3_support_exact_phase_boundaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            ledger=ContributionLedger(Path(temporary)/"ledger.json",USER)
            self.assertEqual(ledger.role(True),"primary")
            ledger.advance(3419,True)
            self.assertEqual(ledger.role(True),"primary")
            ledger.advance(1,True)
            self.assertEqual(ledger.role(True),"support")
            self.assertEqual(ledger.until_switch(True),180)
            ledger.advance(180,True)
            self.assertEqual(ledger.role(True),"primary")
            entry=ledger.data["wallets"][USER]
            self.assertEqual(entry["primary_connected_seconds"],3420)
            self.assertEqual(entry["support_connected_seconds"],180)

    def test_persistent_phase_does_not_count_restart_offline_or_disabled_time(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/"ledger.json"
            ledger=ContributionLedger(path,USER)
            ledger.advance(3430,True)
            ledger.save()
            restored=ContributionLedger(path,USER)
            self.assertEqual(restored.seconds,3430)
            self.assertEqual(restored.role(True),"support")
            restored.advance(99999,False)
            self.assertEqual(restored.seconds,3430)
            self.assertEqual(restored.role(False),"primary")

    def test_corrupt_ledger_disables_contribution_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/"ledger.json"
            path.write_text('{"schema_version":1,"wallets":{"bad":"secret"}}')
            original=path.read_bytes()
            ledger=ContributionLedger(path,USER)
            self.assertFalse(ledger.healthy)
            ledger.advance(9999,True)
            ledger.save()
            self.assertEqual(ledger.role(True),"primary")
            self.assertEqual(path.read_bytes(),original)

    def test_rewrite_only_wallet_auth_and_submit_fields(self):
        message={"id":1,"method":"mining.authorize","params":[USER+".worker","x"]}
        result,auth=rewrite_message(message,USER,AUTHOR_WALLET)
        self.assertTrue(auth)
        self.assertEqual(result["params"],[AUTHOR_WALLET+".worker","x"])
        self.assertEqual(message["params"][0],USER+".worker")
        share={"id":2,"method":"mining.submit","params":[USER+".worker",USER,"nonce"]}
        result,_=rewrite_message(share,USER,AUTHOR_WALLET,authenticated=True)
        self.assertEqual(result["params"],[AUTHOR_WALLET+".worker",USER,"nonce"])
        login={"method":"login","params":{"login":USER,"pass":"x","agent":"SRBMiner"}}
        result,_=rewrite_message(login,USER,AUTHOR_WALLET)
        self.assertEqual(result["params"]["login"],AUTHOR_WALLET)

    def test_unknown_or_wrong_authentication_fails_closed(self):
        for message in ({"method":"custom.login","params":[USER]},
                        {"method":"mining.authorize","params":["unrelated-wallet","x"]},
                        {"method":"login","params":{"login":USER,"wallet":USER}},
                        {"method":"mining.submit","params":[USER,"job","nonce"]}):
            with self.assertRaises(ValueError): rewrite_message(message,USER,AUTHOR_WALLET)

    def test_missing_status_never_claims_actual_contribution(self):
        with tempfile.TemporaryDirectory() as temporary:
            result=read_support_status(temporary,USER,{"support_enabled":True})
            self.assertFalse(result["ready"])
            self.assertFalse(result["connected"])
            self.assertEqual(result["active_wallet"],USER)


class FakePool:
    def __init__(self):
        self.socket=socket.socket()
        self.socket.bind(("127.0.0.1",0))
        self.socket.listen(2)
        self.socket.settimeout(.2)
        self.address=self.socket.getsockname()
        self.stop=threading.Event()
        self.messages=queue.Queue()
        self.thread=threading.Thread(target=self.run,daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            try: connection,_=self.socket.accept()
            except socket.timeout: continue
            except OSError: break
            connection.settimeout(.2)
            buffer=b""
            with connection:
                while not self.stop.is_set():
                    try: data=connection.recv(65536)
                    except socket.timeout: continue
                    except OSError: break
                    if not data: break
                    buffer+=data
                    while b"\n" in buffer:
                        line,buffer=buffer.split(b"\n",1)
                        self.messages.put(json.loads(line))
                        try: connection.sendall(b'{"id":1,"result":true}\n')
                        except OSError: break

    def close(self):
        self.stop.set()
        self.socket.close()
        self.thread.join(2)


class ProxyIntegrationTests(unittest.TestCase):
    def test_pearl_named_wallet_worker_and_large_plain_proof_forwarded_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            settings=root/"settings.json"
            settings.write_text(json.dumps({"wallet":USER,"support_enabled":True}))
            pool=FakePool()
            proxy=LocalStratumProxy(USER,settings,root,connector=lambda:socket.create_connection(pool.address))
            proxy.ledger.advance(3420,True)
            thread=threading.Thread(target=proxy.run,daemon=True)
            thread.start()
            client=None
            try:
                wait_for(lambda:proxy.status["ready"])
                client=socket.create_connection(("127.0.0.1",proxy.status["port"]));client.settimeout(3)
                authorization={"id":1,"method":"mining.authorize","params":{
                    "wallet":USER+".worker","worker":"GPU0","pass":"x","agent":"SRBMiner-MULTI"}}
                client.sendall((json.dumps(authorization)+"\n").encode())
                forwarded=pool.messages.get(timeout=3)
                self.assertEqual(forwarded["params"]["wallet"],AUTHOR_WALLET+".worker")
                self.assertEqual(forwarded["params"]["worker"],"GPU0")
                client.recv(4096)
                proof="A"*1_500_000
                submit={"id":10,"method":"mining.submit","params":{"job_id":"job-large-proof","plain_proof":proof}}
                client.sendall((json.dumps(submit)+"\n").encode())
                forwarded=pool.messages.get(timeout=3)
                self.assertEqual(forwarded,submit)
                self.assertEqual(len(forwarded["params"]["plain_proof"]),1_500_000)
                self.assertTrue(client.recv(4096))
                self.assertIsNone(proxy.status["error"])
            finally:
                proxy.close()
                if client: client.close()
                thread.join(3)
                pool.close()

    def test_author_phase_unknown_auth_closes_without_forwarding_wallet(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            settings=root/"settings.json"
            settings.write_text(json.dumps({"wallet":USER,"support_enabled":True}))
            pool=FakePool()
            proxy=LocalStratumProxy(USER,settings,root,connector=lambda:socket.create_connection(pool.address))
            proxy.ledger.advance(3420,True)
            thread=threading.Thread(target=proxy.run,daemon=True)
            thread.start()
            client=None
            try:
                wait_for(lambda:proxy.status["ready"])
                client=socket.create_connection(("127.0.0.1",proxy.status["port"]));client.settimeout(2)
                client.sendall((json.dumps({"method":"unknown.login","params":[USER]})+"\n").encode())
                self.assertEqual(client.recv(4096),b"")
                self.assertTrue(pool.messages.empty())
                wait_for(lambda:bool(proxy.status.get("error")))
                self.assertIn("desconhecida",proxy.status["error"])
            finally:
                proxy.close()
                if client: client.close()
                thread.join(3)
                pool.close()

    def test_real_local_tcp_auth_submit_switch_disable_and_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            settings=root/"settings.json"
            settings.write_text(json.dumps({"wallet":USER,"support_enabled":True}))
            fake_clock=[0.0]
            pool=FakePool()
            proxy=LocalStratumProxy(USER,settings,root,connector=lambda:socket.create_connection(pool.address),clock=lambda:fake_clock[0])
            thread=threading.Thread(target=proxy.run,daemon=True)
            thread.start()
            client=None
            try:
                wait_for(lambda:proxy.status["ready"])
                port=proxy.status["port"]
                self.assertEqual(proxy.listener.getsockname()[0],"127.0.0.1")
                client=socket.create_connection(("127.0.0.1",port));client.settimeout(2)
                client.sendall((json.dumps({"method":"mining.authorize","params":[USER+".worker","x"]})+"\n").encode())
                self.assertEqual(pool.messages.get(timeout=2)["params"][0],USER+".worker")
                client.recv(4096)
                fake_clock[0]=3421
                self.assertEqual(client.recv(4096),b"")
                client.close()
                client=socket.create_connection(("127.0.0.1",port));client.settimeout(2)
                client.sendall((json.dumps({"method":"mining.authorize","params":[USER,"x"]})+"\n").encode())
                self.assertEqual(pool.messages.get(timeout=2)["params"][0],AUTHOR_WALLET)
                client.recv(4096)
                client.sendall((json.dumps({"method":"mining.submit","params":[USER,"job","nonce"]})+"\n").encode())
                self.assertEqual(pool.messages.get(timeout=2)["params"],[AUTHOR_WALLET,"job","nonce"])
                client.recv(4096)
                settings.write_text(json.dumps({"wallet":USER,"support_enabled":False}))
                self.assertEqual(client.recv(4096),b"")
                client.close()
                client=socket.create_connection(("127.0.0.1",port));client.settimeout(2)
                client.sendall((json.dumps({"method":"login","params":{"login":USER,"pass":"x"}})+"\n").encode())
                self.assertEqual(pool.messages.get(timeout=2)["params"]["login"],USER)
                wait_for(lambda:proxy.status["connected"] and not proxy.status["enabled"])
                self.assertEqual(proxy.status["active_role"],"primary")
            finally:
                proxy.close()
                if client: client.close()
                thread.join(3)
                pool.close()
            self.assertFalse(thread.is_alive())
            final=json.loads((root/"support-mining.json").read_text())
            self.assertFalse(final["ready"])
            self.assertFalse(final["connected"])

    def test_malformed_settings_hot_disables_author_routing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            settings=root/"settings.json"
            settings.write_text('{broken')
            proxy=LocalStratumProxy(USER,settings,root)
            proxy.ledger.advance(3420,True)
            self.assertFalse(proxy.configuration()["enabled"])
            self.assertFalse(proxy.configuration()["configuration_valid"])


if __name__=="__main__":
    unittest.main()
