"""Apoio comum da suíte: marca explícita para testes que exigem APIs do Windows.

O produto é exclusivo do Windows. Fora dele (`tools/run_tests.py`), esses
testes aparecem como pulados com o motivo, em vez de falhas ou erros de import.
No Windows, nada muda: todos executam.
"""

import sys
import unittest

IS_WINDOWS = sys.platform == "win32"


def windows_only(reason):
    return unittest.skipUnless(IS_WINDOWS, "Somente Windows: " + reason)
