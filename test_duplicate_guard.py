import json
import subprocess
import unittest
from unittest.mock import patch

from rig import ProcessBackend,windows_argv


class DuplicateGuardTests(unittest.TestCase):
    def check(self,rows,index=0,returncode=0):
        result=subprocess.CompletedProcess([],returncode,json.dumps(rows),'')
        with patch('rig.subprocess.run',return_value=result):
            ProcessBackend().assert_gpu_available('SRBMiner-MULTI.exe',index)

    def test_empty_commandline_never_becomes_python_identity(self):
        for value in ('',' ',None): self.assertEqual(windows_argv(value),[])

    def test_same_gpu_or_multiple_selection_is_blocked(self):
        for command in ('miner.exe --gpu-id 0','miner.exe --gpu-id 1,0'):
            with self.assertRaises(RuntimeError): self.check([{'ProcessId':42,'CommandLine':command}])

    def test_missing_unreadable_or_invalid_selection_is_blocked(self):
        for command in (None,'','miner.exe','miner.exe --gpu-id','miner.exe --gpu-id invalid'):
            with self.assertRaises(RuntimeError): self.check([{'ProcessId':42,'CommandLine':command}])

    def test_other_gpu_is_preserved_and_does_not_block(self):
        self.check([{'ProcessId':42,'CommandLine':'miner.exe --gpu-id 1,2'}])

    def test_no_existing_miner_allows_start(self):
        self.check([])

    def test_failed_inventory_blocks_start(self):
        with self.assertRaises(RuntimeError): self.check([],returncode=1)


if __name__=='__main__': unittest.main()
