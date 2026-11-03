"""Regras da janela única de Configurações (v0.5.8), sem abrir janelas reais."""
import time
import tkinter as tk
import unittest

from config import DEFAULT_WALLET
from operations_ui import console_python, service_update_progress
from settings_ui import button_role, needs_restart, wallet_feedback
from ui_host import Section, embedded, open_window

OTHER = "prl1" + "q"*40


class WalletTests(unittest.TestCase):
    def test_live_feedback_distinguishes_invalid_author_and_own_wallet(self):
        self.assertEqual(wallet_feedback("")[0], "invalid")
        self.assertEqual(wallet_feedback("bc1qxyz")[0], "invalid")
        self.assertEqual(wallet_feedback(DEFAULT_WALLET)[0], "author")
        self.assertEqual(wallet_feedback("  "+OTHER+" ")[0], "valid")

    def test_only_wallet_or_log_folder_require_panel_restart(self):
        base = {"wallet": OTHER, "log_dir": "C:/logs", "support_enabled": True}
        self.assertFalse(needs_restart(base, {**base, "support_enabled": False}))
        self.assertTrue(needs_restart(base, {**base, "wallet": DEFAULT_WALLET}))
        self.assertTrue(needs_restart(base, {**base, "log_dir": "D:/logs"}))


class ThemeTests(unittest.TestCase):
    def test_button_roles(self):
        self.assertEqual(button_role("Salvar IA", "SystemButtonFace"), "primary")
        self.assertEqual(button_role("Qualquer", "#12A98E"), "primary")
        self.assertEqual(button_role("Remover exceção (UAC)", ""), "danger")
        self.assertEqual(button_role("Verificar agora", ""), "secondary")


class ServiceUpdateTests(unittest.TestCase):
    def test_progress_reports_waiting_done_and_failed_honestly(self):
        now = time.time()
        before = {"pid": 10, "sampled_at": now, "ready": True}
        self.assertIn("Aguardando", service_update_progress(before, before, 5)[1])
        self.assertEqual(service_update_progress(before, {"pid": 10, "sampled_at": now-60}, 30)[0], "waiting")
        done = service_update_progress(before, {"pid": 11, "sampled_at": now, "ready": True, "software": {"version": "v9"}}, 40)
        self.assertEqual(done[0], "done")
        self.assertIn("v9", done[1])
        self.assertEqual(service_update_progress(before, {"pid": 10, "sampled_at": now-60}, 241)[0], "failed")

    def test_service_scripts_get_console_python(self):
        self.assertTrue(console_python().lower().endswith(("python.exe", "python")))


class SectionHostTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk indisponível: {error}")
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()

    def test_closing_inside_a_section_rebuilds_instead_of_destroying(self):
        closed = []
        container = tk.Frame(self.root)
        container.section_closed = closed.append
        container.navigate = lambda name: closed.append(name)
        window = open_window(self.root, container, "Teste")
        self.assertTrue(embedded(window))
        window.title("x"); window.geometry("10x10"); window.transient(self.root); window.grab_set()

        window.destroy()
        self.assertEqual(closed, [window])
        self.assertTrue(window.winfo_exists())
        window.navigate("GPUs")
        self.assertEqual(closed[-1], "GPUs")
        window.release = True
        window.destroy()
        self.assertFalse(window.winfo_exists())

    def test_without_container_it_is_a_real_window(self):
        window = open_window(self.root, None, "Janela")
        self.assertIsInstance(window, tk.Toplevel)
        self.assertNotIsInstance(window, Section)
        window.destroy()


if __name__ == "__main__":
    unittest.main()
