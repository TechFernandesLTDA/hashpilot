"""Wire-level audit: local sockets and synthetic wallets only; no real mining."""
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from contribution import AUTHOR_WALLET, ContributionLedger, LocalStratumProxy, rewrite_message
from test_contribution import FakePool, USER, wait_for


class ContributionSafety(unittest.TestCase):
    def test_non_mining_actions_cannot_pass_even_after_authorization(self):
        for method in ('wallet.transfer','withdraw','set_payout_address','custom.login'):
            with self.assertRaises(ValueError):
                rewrite_message({'id':3,'method':method,'params':{'wallet':AUTHOR_WALLET}},USER,AUTHOR_WALLET,authenticated=True)
    def test_reservation_storage_failure_cannot_forward_author_auth(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);settings=root/'settings.json'
            settings.write_text(json.dumps({'wallet':USER,'support_enabled':True}))
            proxy=LocalStratumProxy(USER,settings,root)
            proxy.ledger.record_route(5,'primary')
            proxy.ledger.data['wallets'][USER]['verified_primary_seconds']=3420
            proxy.ledger.data['wallets'][USER]['primary_connected_seconds']=3420
            with patch.object(proxy.ledger,'save',side_effect=OSError('injected storage failure')), patch.object(proxy,'_send') as send:
                with self.assertRaises(OSError):proxy.serve_connection(None,None)
                send.assert_not_called()

    def test_real_clock_scaled_tcp_cycle_routes_user_author_user_and_records_acks(self):
        with tempfile.TemporaryDirectory() as temporary, patch('contribution.PRIMARY_SECONDS',1.9), patch('contribution.SUPPORT_SECONDS',.1):
            root=Path(temporary);settings=root/'settings.json'
            settings.write_text(json.dumps({'wallet':USER,'support_enabled':True}))
            pool=FakePool()
            proxy=LocalStratumProxy(USER,settings,root,connector=lambda:socket.create_connection(pool.address))
            thread=threading.Thread(target=proxy.run,daemon=True);thread.start()
            client=None
            def connect(expected):
                connection=socket.create_connection(('127.0.0.1',proxy.status['port']));connection.settimeout(4)
                connection.sendall((json.dumps({'id':1,'method':'mining.authorize','params':{'wallet':USER,'worker':'fixture'}})+'\n').encode())
                self.assertEqual(pool.messages.get(timeout=2)['params']['wallet'],expected)
                self.assertTrue(connection.recv(4096))
                return connection
            try:
                wait_for(lambda:proxy.status['ready'])
                client=connect(USER)
                self.assertEqual(client.recv(4096),b'');client.close()
                client=connect(AUTHOR_WALLET)
                submit={'id':2,'method':'mining.submit','params':{'job_id':'fixture','plain_proof':'not-real-pow'}}
                client.sendall((json.dumps(submit)+'\n').encode())
                self.assertEqual(pool.messages.get(timeout=2),submit)
                self.assertTrue(client.recv(4096))
                self.assertEqual(client.recv(4096),b'');client.close()
                client=connect(USER)
                entry=proxy.ledger.data['wallets'][USER]
                self.assertEqual(entry.get('accepted_support_shares'),1)
                self.assertLessEqual(entry['reserved_support_seconds']/(entry['verified_primary_seconds']+entry['reserved_support_seconds']),.05)
            finally:
                proxy.close()
                if client:client.close()
                thread.join(2);pool.close()

    def test_rejected_author_auth_falls_back_to_user_without_reusing_quota(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);settings=root/'settings.json'
            settings.write_text(json.dumps({'wallet':USER,'support_enabled':True}))
            def respond(message):
                wallet=message.get('params',{}).get('wallet')
                return {'id':message['id'],'result':wallet!=AUTHOR_WALLET,'error':None if wallet!=AUTHOR_WALLET else {'code':25}}
            pool=FakePool(respond)
            proxy=LocalStratumProxy(USER,settings,root,connector=lambda:socket.create_connection(pool.address))
            proxy.ledger.advance(3420,True)
            proxy.ledger.data['wallets'][USER]['verified_primary_seconds']=3420
            thread=threading.Thread(target=proxy.run,daemon=True);thread.start()
            client=None
            try:
                wait_for(lambda:proxy.status['ready'])
                for expected in (AUTHOR_WALLET,USER,USER):
                    client=socket.create_connection(('127.0.0.1',proxy.status['port']));client.settimeout(2)
                    client.sendall((json.dumps({'id':1,'method':'mining.authorize','params':{'wallet':USER}})+'\n').encode())
                    self.assertEqual(pool.messages.get(timeout=2)['params']['wallet'],expected)
                    if expected==AUTHOR_WALLET:self.assertEqual(client.recv(4096),b'')
                    else:self.assertTrue(client.recv(4096))
                    client.close();client=None
                self.assertTrue(proxy.safety_disabled)
                self.assertEqual(proxy.ledger.data['wallets'][USER]['reserved_support_seconds'],180)
            finally:
                proxy.close()
                if client:client.close()
                thread.join(2);pool.close()

    def test_prepaid_windows_cannot_be_reused_after_crash_or_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'ledger.json'
            ledger=ContributionLedger(path,USER)
            for cycle in range(1,11):
                for _ in range(684):ledger.record_route(5,'primary')
                self.assertEqual(ledger.reserve_support(),180)
                # Restart immediately after reservation: the complete quota is spent,
                # even if no author share was sent before the crash.
                ledger=ContributionLedger(path,USER)
                self.assertTrue(ledger.healthy)
                self.assertEqual(ledger.reserve_support(),0)
                entry=ledger.data['wallets'][USER]
                self.assertLessEqual(entry['reserved_support_seconds']/
                    (entry['verified_primary_seconds']+entry['reserved_support_seconds']),.05)
                self.assertEqual(entry['reserved_support_seconds'],cycle*180)

    def test_legacy_elapsed_time_does_not_retroactively_earn_author_quota(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'ledger.json'
            ledger=ContributionLedger(path,USER)
            ledger.advance(3420,True);ledger.save()
            restored=ContributionLedger(path,USER)
            self.assertEqual(restored.available_support(),0)

    def test_legacy_migration_preserves_history_but_issues_v2_no_retroactive_quota(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'ledger.json'
            path.write_text(json.dumps({'schema_version':1,'wallets':{USER:{'eligible_connected_seconds':3420,
                'primary_connected_seconds':3420,'support_connected_seconds':0,'verified_primary_seconds':3420}}}))
            ledger=ContributionLedger(path,USER)
            self.assertTrue(ledger.healthy)
            self.assertEqual(ledger.seconds,3420)
            self.assertEqual(ledger.available_support(),0)
            ledger.save()
            self.assertEqual(json.loads(path.read_text())['schema_version'],2)

    def test_invalid_quota_fails_in_favor_of_user_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'ledger.json'
            ledger=ContributionLedger(path,USER)
            ledger.data['wallets'][USER]['reserved_support_seconds']=180
            ledger.save();original=path.read_bytes()
            restored=ContributionLedger(path,USER)
            self.assertFalse(restored.healthy)
            self.assertEqual(restored.available_support(),0)
            restored.save()
            self.assertEqual(path.read_bytes(),original)
            entry=ledger.data['wallets'][USER]
            entry.update(verified_primary_seconds=34200,primary_connected_seconds=34200,reserved_support_seconds=0)
            ledger.save()
            self.assertFalse(ContributionLedger(path,USER).healthy)

    def test_idle_pre_authorization_does_not_earn_donation_quota(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            settings=root/'settings.json'
            settings.write_text(json.dumps({'wallet':USER,'support_enabled':True}))
            clock=[0.0]
            proxy=LocalStratumProxy(USER,settings,root,clock=lambda:clock[0])
            client,local=socket.socketpair()
            upstream,pool=socket.socketpair()
            errors=[]
            def run():
                try:proxy.serve_connection(local,upstream)
                except Exception as error:errors.append(error)
                finally:local.close();upstream.close()
            thread=threading.Thread(target=run,daemon=True);thread.start()
            try:
                wait_for(lambda:proxy.status['connected'])
                clock[0]=3400
                client.sendall(b'{"id":2,"method":"mining.subscribe","params":[]}\n')
                pool.settimeout(2);pool.recv(4096)
                wait_for(lambda:proxy.status.get('sampled_at') is not None)
            finally:
                proxy.close();client.close();pool.close();thread.join(2)
            self.assertEqual(proxy.ledger.seconds,0,'TCP idle time was counted as user mining time')

    def test_author_session_cannot_survive_a_full_cycle_clock_jump(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            settings=root/'settings.json'
            settings.write_text(json.dumps({'wallet':USER,'support_enabled':True}))
            clock=[0.0]
            pool=FakePool()
            proxy=LocalStratumProxy(USER,settings,root,connector=lambda:socket.create_connection(pool.address),clock=lambda:clock[0])
            proxy.ledger.advance(3420,True)
            # New quota accounting uses confirmed user time, independent of old phases.
            entry=proxy.ledger.data['wallets'][USER]
            entry['verified_primary_seconds']=3420.0
            thread=threading.Thread(target=proxy.run,daemon=True);thread.start()
            client=None
            try:
                wait_for(lambda:proxy.status['ready'])
                client=socket.create_connection(('127.0.0.1',proxy.status['port']));client.settimeout(2)
                client.sendall((json.dumps({'id':1,'method':'mining.authorize','params':[USER,'x']})+'\n').encode())
                self.assertEqual(pool.messages.get(timeout=2)['params'][0],AUTHOR_WALLET)
                client.recv(4096)
                clock[0]=3600
                time.sleep(.35)
                self.assertEqual(client.recv(4096),b'','Author connection survived its time allowance')
            finally:
                proxy.close()
                if client:client.close()
                thread.join(2);pool.close()


if __name__=='__main__':unittest.main()
