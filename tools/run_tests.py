"""Executa a suíte unitária em qualquer sistema, com estado isolado.

No Windows equivale a `python -m unittest discover`. Fora dele, aponta
`HASHPILOT_TEST_STATE_DIR` para um diretório temporário, para que módulos que
importam `config.py` carreguem; testes que dependem de APIs do Windows são
pulados explicitamente (`windows_only`). Nada aqui inicia minerador ou altera GPU.

Uso: python tools/run_tests.py [-v] [padrão ou módulo...]
"""
from __future__ import annotations
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str]) -> int:
    verbosity = 2 if "-v" in argv else 1
    names = [item for item in argv if item != "-v"]
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    with tempfile.TemporaryDirectory(prefix="hashpilot-tests-") as state:
        if sys.platform != "win32":
            os.environ.setdefault("HASHPILOT_TEST_STATE_DIR", state)
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromNames(names) if names else loader.discover(str(ROOT), pattern="test_*.py", top_level_dir=str(ROOT))
        result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
