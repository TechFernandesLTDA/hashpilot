"""Ferramenta de inspeção de release do SRBMiner: só lê bytes, nunca extrai ou executa."""

import hashlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("inspect_miner_release", ROOT / "tools/inspect_miner_release.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

BINARY = b"MZ fixture only, never executed"


def package(*members):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, data in members:
            archive.writestr(name, data)
    return stream.getvalue()


class InspectMinerReleaseTests(unittest.TestCase):
    def test_nested_layout_yields_catalog_entry_with_member_and_hashes(self):
        data = package(
            ("SRBMiner-Multi-3-8-6/Win/SRBMiner-Multi-3-8-6/SRBMiner-MULTI.exe", BINARY), ("readme.txt", b"x")
        )
        digest = hashlib.sha256(data).hexdigest()
        report = tool.inspect("3.8.6", data, digest, hashlib.md5(data).hexdigest())
        entry = report["catalog_entry"]
        self.assertEqual(entry["archive_sha256"], digest)
        self.assertEqual(entry["exe_sha256"], hashlib.sha256(BINARY).hexdigest())
        self.assertEqual(entry["archive_member"], "SRBMiner-Multi-3-8-6/Win/SRBMiner-Multi-3-8-6/SRBMiner-MULTI.exe")
        self.assertTrue(report["url"].endswith("/3.8.6/SRBMiner-Multi-3-8-6-win64.zip"))

    def test_default_layout_omits_archive_member(self):
        data = package(("SRBMiner-Multi-3-8-6/SRBMiner-MULTI.exe", BINARY))
        entry = tool.inspect("3.8.6", data, hashlib.sha256(data).hexdigest())["catalog_entry"]
        self.assertNotIn("archive_member", entry)

    def test_refuses_wrong_digest_md5_ambiguous_or_unsafe_packages(self):
        data = package(("SRBMiner-Multi-3-8-6/SRBMiner-MULTI.exe", BINARY))
        with self.assertRaisesRegex(ValueError, "SHA-256 do pacote divergente"):
            tool.inspect("3.8.6", data, "0" * 64)
        with self.assertRaisesRegex(ValueError, "MD5"):
            tool.inspect("3.8.6", data, hashlib.sha256(data).hexdigest(), "0" * 32)
        double = package(("a/SRBMiner-MULTI.exe", BINARY), ("b/SRBMiner-MULTI.exe", BINARY))
        with self.assertRaisesRegex(ValueError, "exatamente um"):
            tool.inspect("3.8.6", double, hashlib.sha256(double).hexdigest())
        unsafe = package(("../SRBMiner-MULTI.exe", BINARY))
        with self.assertRaisesRegex(ValueError, "inseguro"):
            tool.inspect("3.8.6", unsafe, hashlib.sha256(unsafe).hexdigest())
        with self.assertRaisesRegex(ValueError, "formato"):
            tool.inspect("3.8", data, hashlib.sha256(data).hexdigest())

    def test_reads_local_file_without_extracting_anything(self):
        data = package(("SRBMiner-Multi-3-8-6/SRBMiner-MULTI.exe", BINARY))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pacote.zip"
            path.write_bytes(data)
            self.assertEqual(
                tool.main(["3.8.6", "--archive-sha256", hashlib.sha256(data).hexdigest(), "--file", str(path)]), 0
            )
            self.assertEqual(sorted(p.name for p in Path(folder).iterdir()), ["pacote.zip"])


if __name__ == "__main__":
    unittest.main()
