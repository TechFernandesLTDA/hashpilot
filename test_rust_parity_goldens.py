"""As respostas de referência do port em Rust precisam refletir o Python atual.

Regera os goldens num diretório temporário e compara com os commitados em
rust/hashpilot-core/tests/golden/. Se este teste falhar, o comportamento do
Python mudou: rode `python tools/rust_parity/generate_goldens.py`, revise o diff
e ajuste o Rust até `cargo test` voltar a passar.
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


@unittest.skipIf(
    sys.platform == "win32", "Goldens são gerados com TZ=UTC e caminhos POSIX (time.tzset não existe no Windows)"
)
class RustParityGoldenFreshnessTests(unittest.TestCase):
    def test_committed_goldens_match_current_python(self):
        with tempfile.TemporaryDirectory() as folder:
            env = {**os.environ, "HASHPILOT_GOLDEN_OUT": folder}
            result = subprocess.run(
                [sys.executable, str(ROOT / "tools/rust_parity/generate_goldens.py")],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            fresh = sorted(path.name for path in Path(folder).glob("*.json"))
            self.assertEqual(fresh, sorted(path.name for path in GOLDEN.glob("*.json")))
            self.assertGreaterEqual(len(fresh), 7)
            for name in fresh:
                with self.subTest(golden=name):
                    regenerated = json.loads((Path(folder) / name).read_text(encoding="utf-8"))
                    committed = json.loads((GOLDEN / name).read_text(encoding="utf-8"))
                    self.assertEqual(regenerated, committed)


if __name__ == "__main__":
    unittest.main()
