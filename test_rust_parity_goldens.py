"""As respostas de referência do port em Rust precisam refletir o Python atual.

Regera os goldens num diretório temporário e compara com os commitados em
rust/hashpilot-core/tests/golden/. Se este teste falhar, o comportamento do
Python mudou: rode `python tools/rust_parity/generate_goldens.py`, revise o diff
e ajuste o Rust até `cargo test` voltar a passar.

No Linux confere todos os goldens, menos os que usam a API do Windows. No Windows
(sem `time.tzset`) confere os que não dependem do fuso e os da API do Windows.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
GOLDEN = ROOT / "rust/hashpilot-core/tests/golden"
GENERATOR = ROOT / "tools/rust_parity/generate_goldens.py"


def generator_sets():
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--list"], cwd=ROOT, capture_output=True, text=True, timeout=60
    )
    if result.returncode:
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout)


class RustParityGoldenFreshnessTests(unittest.TestCase):
    def test_committed_goldens_match_current_python(self):
        sets = generator_sets()
        here = set(sets["here"])
        with tempfile.TemporaryDirectory() as folder:
            env = {**os.environ, "HASHPILOT_GOLDEN_OUT": folder}
            result = subprocess.run(
                [sys.executable, str(GENERATOR)],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            fresh = sorted(path.name for path in Path(folder).glob("*.json"))
            self.assertEqual(fresh, sorted(f"{name}.json" for name in here))
            committed_names = {path.stem for path in GOLDEN.glob("*.json")}
            if sys.platform != "win32":
                # Os goldens da API do Windows não são regerados aqui, mas precisam existir.
                self.assertEqual(committed_names, here | set(sets["windows_only"]))
            self.assertTrue(here <= committed_names, sorted(here - committed_names))
            self.assertGreaterEqual(len(fresh), 3)
            for name in fresh:
                with self.subTest(golden=name):
                    regenerated = json.loads((Path(folder) / name).read_text(encoding="utf-8"))
                    committed = json.loads((GOLDEN / name).read_text(encoding="utf-8"))
                    self.assertEqual(regenerated, committed)


if __name__ == "__main__":
    unittest.main()
