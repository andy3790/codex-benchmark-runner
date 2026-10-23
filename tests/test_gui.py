import copy
import contextlib
import io
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.helpers import setup_experiment, test_directory

try:
    import tkinter as tk
    from codex_bench.gui import BenchmarkApp
except ImportError:
    tk = None


@unittest.skipIf(tk is None, "Tkinter is optional on headless test hosts")
class GuiSmokeTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.temp = test_directory()
        from pathlib import Path
        self.cfg = setup_experiment(Path(self.temp.name))
        self.errors = patch("codex_bench.gui.messagebox.showerror", side_effect=AssertionError("Unexpected GUI error"))
        self.errors.start()
        self.app = BenchmarkApp(self.root, self.cfg.path)
        self.root.update_idletasks()

    def tearDown(self):
        if self.app.controller.busy:
            self.app.controller.stop()
            self.app.controller.thread.join(10)
        permitted = Path(tempfile.gettempdir()) / "codex-bench-test-workspaces"
        if self.cfg.scratch.is_dir() and self.cfg.scratch.resolve().is_relative_to(permitted.resolve()) and self.cfg.scratch.resolve() != permitted.resolve():
            shutil.rmtree(self.cfg.scratch)
        self.errors.stop()
        self.root.destroy()
        self.temp.cleanup()

    def test_load_edit_and_save_roundtrip(self):
        self.assertEqual(len(self.app.model_tree.get_children()), 1)
        self.assertEqual(len(self.app.result_tree.get_children()), 2)
        self.app.variables["repetitions"].set("2")
        self.assertIn("4", self.app.plan_label.get())
        self.app.prompt.insert("end", "GUI 편집")
        self.assertTrue(self.app.save())
        self.assertEqual(self.app.preview().raw["repetitions"], 2)
        self.assertIn("GUI 편집", self.app.preview().prompt)

    def test_busy_controls_and_missing_metrics(self):
        self.app._set_busy(True)
        self.assertEqual(str(self.app.variables["mode"].get()), "sequential")
        self.assertEqual(str(self.app.stop_button["state"]), "normal")
        self.app._set_busy(False)
        self.assertEqual(str(self.app.stop_button["state"]), "disabled")
        first = self.app.result_tree.get_children()[0]
        values = self.app.result_tree.item(first, "values")
        self.assertEqual(values[4:7], ("—", "—", "—"))

    def test_report_button_uses_local_file_uri(self):
        path = self.cfg.path.parent / "report.html"
        path.write_text("<!doctype html><title>fixture</title>")
        self.app.controller.last_report = path
        with patch("codex_bench.gui.webbrowser.open", return_value=True) as browser:
            self.app.open_report()
        self.assertEqual(browser.call_args.args[0], path.resolve().as_uri())

    def test_minimum_window_keeps_controls_and_log_visible(self):
        self.root.attributes("-alpha", 0.0)
        self.root.geometry("1060x720")
        self.root.deiconify()
        self.root.update()
        for widget in [self.app.stop_button, self.app.report_button, self.app.result_tree, self.app.log_text]:
            with self.subTest(widget=widget):
                self.assertTrue(widget.winfo_ismapped())
                x = widget.winfo_rootx() - self.root.winfo_rootx()
                y = widget.winfo_rooty() - self.root.winfo_rooty()
                self.assertGreaterEqual(x, 0)
                self.assertGreaterEqual(y, 0)
                self.assertLessEqual(x + widget.winfo_width(), self.root.winfo_width())
                self.assertLessEqual(y + widget.winfo_height(), self.root.winfo_height())
                self.assertGreater(widget.winfo_height(), 20)

    def test_mock_action_updates_table_logs_and_report_in_event_loop(self):
        with patch("codex_bench.gui_controller.diagnose", side_effect=AssertionError("No CLI calls in mock mode")), contextlib.redirect_stdout(io.StringIO()):
            self.app.start("mock")
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                self.root.update()
                if not self.app.controller.busy and self.app.controller.events.empty():
                    break
                time.sleep(0.025)
        self.assertFalse(self.app.controller.busy)
        self.assertTrue(self.app.controller.last_report.is_file())
        self.assertIn("완료 2 / 2", self.app.counter.get())
        self.assertIn("모의 실행 종료", self.app.status.get())
        self.assertIn("합성 데이터", self.app.log_text.get("1.0", "end"))
        self.assertEqual({row["status"] for row in self.app.rows.values()}, {"succeeded", "unsupported"})
        # Refresh retains the last mock result instead of replacing it with the real output.
        self.app.refresh_results()
        self.assertIn("완료 2 / 2", self.app.counter.get())
