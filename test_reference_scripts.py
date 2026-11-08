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
        start = text("Iniciar-Referencia-RTX3070.ps1")
        self.assertLess(start.index("Assert-ReferenceRtx3070"), start.index("Start-Process -FilePath $miner"))


class ReferenceGuardTests(unittest.TestCase):
    def test_gpu_guard_uses_the_same_unit_as_the_python_profile(self):
        from reference_profile import REFERENCE_RTX3070_UUID

        helper = text("Referencia-Comum.ps1")
        self.assertIn(f"$ReferenceRtx3070Uuid='{REFERENCE_RTX3070_UUID}'", helper)
        guard = helper[
            helper.index("function Assert-ReferenceRtx3070") : helper.index("function Get-ReferenceMinerPath")
        ]
        self.assertIn("$fields[1] -ne $ReferenceRtx3070Uuid", guard)
        self.assertIn("$power-240", guard)

    def test_wallet_follows_gpu_override_and_respects_enabled_contribution(self):
        helper = text("Referencia-Comum.ps1")
        wallet = helper[helper.index("function Get-ReferenceWallet") :]
        self.assertIn("gpu_settings", wallet)
        self.assertLess(wallet.index("gpu_settings"), wallet.index("Assert-ReferenceContribution $wallet"))
        self.assertIn("support_enabled -eq $true", helper)
        self.assertIn("else{Assert-ReferenceContribution $Wallet}", text("Iniciar-Referencia-RTX3070.ps1"))

    def test_every_launcher_records_its_miner_before_starting_and_stop_uses_the_records(self):
        for name in ("Iniciar-Referencia-RTX3070.ps1", "Maximo-Pearl.ps1", "Turbo-Pearl.ps1", "Desempenho-Pearl.ps1"):
            with self.subTest(script=name):
                content = text(name)
                self.assertLess(
                    content.index("Register-ReferenceMiner $miner"), content.index("Start-Process -FilePath $miner")
                )
        stop = text("Parar-Referencia-RTX3070.ps1")
        self.assertIn("Get-ReferenceMinerPaths $base", stop)
        self.assertIn("$referenceMiners -contains $_.ExecutablePath", stop)
        self.assertNotIn("SRBMiner-Multi-*", stop)

    def test_historical_cutoffs_never_exceed_the_reference_backstop(self):
        # O fluxo historico usa 84 C como corte do minerador, acima da emergencia do controlador (83 C).
        from thermal import EMERGENCY_C

        for path in SCRIPTS.glob("*.ps1"):
            for value in re.findall(r"'--gpu-off-temperature','(\d+)'", path.read_text(encoding="utf-8-sig")):
                with self.subTest(script=path.name, value=value):
                    self.assertLessEqual(int(value), EMERGENCY_C + 1)

    def test_powershell_structure_is_balanced(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("ps_structure", ROOT / "tools/check_powershell_structure.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        scripts = sorted(p for p in ROOT.rglob("*.ps1") if "dist" not in p.relative_to(ROOT).parts)
        self.assertGreater(len(scripts), 20)  # instalador, tools/ e mining/windows/
        for path in scripts:
            with self.subTest(script=str(path.relative_to(ROOT))):
                module.check(path.read_text(encoding="utf-8-sig"))
        with self.assertRaises(AssertionError):
            module.check("if($a){Write-Host 'x'\n")


if __name__ == "__main__":
    unittest.main()
