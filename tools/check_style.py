"""Lint (ruff), formatação dos módulos novos e tipos (mypy), sem rede e sem executar o app.

Ferramentas de desenvolvimento opcionais: o aplicativo continua só com a
biblioteca padrão. Se ruff ou mypy não estiverem instalados, a etapa é
informada como não executada e o código de saída é 0 (use --strict para falhar).

Uso: python tools/check_style.py [--strict]
Instalação sugerida: pip install ruff mypy
"""

from __future__ import annotations
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
# Módulos que já seguem `ruff format`; ao criar um módulo novo, acrescente-o aqui.
FORMATTED = [
    "app_logging.py",
    "config.py",
    "data_contracts.py",
    "fsutil.py",
    "miner_log.py",
    "monitor_theme.py",
    "nvidia_smi.py",
    "testing_support.py",
    "test_app_logging.py",
    "test_config.py",
    "test_miner_log.py",
    "test_monitor_log.py",
    "test_monitor_shutdown.py",
    "test_reference_scripts.py",
    "test_rust_parity_goldens.py",
    "tools/rust_parity/generate_goldens.py",
    "tools/check_powershell_structure.py",
    "tools/check_style.py",
    "tools/run_tests.py",
]


def run(name: str, arguments: list[str], strict: bool) -> bool:
    executable = shutil.which(name)
    if not executable:
        print(f"{name} não encontrado; etapa não executada", file=sys.stderr)
        return not strict
    result = subprocess.run([executable, *arguments], cwd=ROOT)
    return result.returncode == 0


def main(argv: list[str]) -> int:
    strict = "--strict" in argv
    steps = [
        ("ruff", ["check", "."]),
        ("ruff", ["format", "--check", *FORMATTED]),
        ("mypy", []),
    ]
    ok = all([run(name, arguments, strict) for name, arguments in steps])
    print("estilo e tipos: " + ("ok" if ok else "falhou"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
