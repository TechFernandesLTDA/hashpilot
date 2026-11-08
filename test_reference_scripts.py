"""Coerência entre o catálogo do minerador (Python) e os scripts PowerShell."""

import json
import re
import unittest
from pathlib import Path

from contribution import AUTHOR_WALLET
from miner_update import CATALOG, DEFAULT_VERSION, descriptor

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / "mining/windows"


def text(name):
    return (SCRIPTS / name).read_text(encoding="utf-8-sig")


class MinerVersionSourceTests(unittest.TestCase):
    def test_downloads_manifest_is_the_default_catalog_entry(self):
        manifest = json.loads(text("downloads.json"))["srbminer"]
        item = descriptor(DEFAULT_VERSION)
        self.assertEqual(manifest["version"], DEFAULT_VERSION)
        self.assertEqual(manifest["folder"], item["folder"])
        self.assertEqual(manifest["url"], item["url"])
        self.assertEqual(manifest["sha256"], item["archive_sha256"])
        self.assertEqual(manifest["exe_sha256"], item["exe_sha256"])

    def test_powershell_version_lists_match_catalog(self):
        helper = text("Referencia-Comum.ps1")
        allowed = re.search(r"\$ReferenceAllowedMinerVersions=@\(([^)]*)\)", helper)[1]
        self.assertEqual(re.findall(r"'([\d.]+)'", allowed), list(CATALOG))
        self.assertIn(f"$ReferenceDefaultMinerVersion='{DEFAULT_VERSION}'", helper)
        defender = text("Permissao-Defender.ps1")
        for version, item in CATALOG.items():
            self.assertIn(f"'{version}'", defender)
            self.assertIn(item["exe_sha256"], defender)

    def test_no_script_pins_a_miner_folder_or_author_wallet_outside_the_helper(self):
        for path in SCRIPTS.glob("*.ps1"):
            if path.name == "Referencia-Comum.ps1":
                continue
            with self.subTest(script=path.name):
                content = path.read_text(encoding="utf-8-sig")
                self.assertNotIn("SRBMiner-Multi-3-8-0", content)
                self.assertNotIn(AUTHOR_WALLET, content)
        self.assertIn(f"$ReferenceAuthorWallet='{AUTHOR_WALLET}'", text("Referencia-Comum.ps1"))

    def test_clock_writing_reference_scripts_check_the_gpu_first(self):
        for name in ("Maximo-Pearl.ps1", "Turbo-Pearl.ps1", "Desempenho-Pearl.ps1"):
            with self.subTest(script=name):
                content = text(name)
                self.assertLess(content.index("Assert-ReferenceRtx3070"), content.index("nvidia-smi -i 0 -pl"))
                self.assertIn("'--wallet',$wallet", content)


if __name__ == "__main__":
    unittest.main()
