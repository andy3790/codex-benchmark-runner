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

    def test_browse_prompt_then_external_change_or_deletion_blocks_save(self):
        target = self.cfg.path.parent / "selected.md"
        config_before = self.cfg.path.read_bytes()
        original_before = self.cfg.prompt_path.read_bytes()
        for deleted in (False, True):
            with self.subTest(deleted=deleted):
                target.write_text("선택한 내용\n", encoding="utf-8")
                with patch("codex_bench.gui.filedialog.askopenfilename", return_value=str(target)):
                    self.app.browse("prompt_file", "file")
                self.assertEqual(self.app._prompt(), "선택한 내용\n")
                self.app.prompt.insert("end", "GUI 편집")
                if deleted:
                    target.unlink()
                else:
                    target.write_text("external edit", encoding="utf-8")
                with patch("codex_bench.gui.messagebox.showerror") as error:
                    self.assertFalse(self.app.save())
                error.assert_called_once()
                self.assertEqual(self.cfg.path.read_bytes(), config_before)
                self.assertEqual(self.cfg.prompt_path.read_bytes(), original_before)
                if deleted:
                    self.assertFalse(target.exists())
                else:
                    self.assertEqual(target.read_text(encoding="utf-8"), "external edit")

    def test_browse_prompt_edit_and_save_roundtrip(self):
        target = self.cfg.path.parent / "selected.md"
        target.write_bytes("선택한 내용\r\n".encode("utf-8-sig"))
        with patch("codex_bench.gui.filedialog.askopenfilename", return_value=str(target)):
            self.app.browse("prompt_file", "file")
        self.assertEqual(self.app._prompt(), "선택한 내용\n")
        self.app.prompt.insert("end", "GUI 편집\n")
        self.assertTrue(self.app.save())
        self.assertEqual(self.app.preview().prompt_path, target)
        self.assertEqual(target.read_bytes(), "선택한 내용\nGUI 편집\n".encode("utf-8"))
        before = target.read_bytes(), self.cfg.path.read_bytes()
        self.assertTrue(self.app.save())
        self.assertEqual((target.read_bytes(), self.cfg.path.read_bytes()), before)

    def test_cancel_prompt_browser_preserves_selection_and_change_detection(self):
        target = self.cfg.path.parent / "selected.md"
        target.write_text("selected content", encoding="utf-8")
        with patch("codex_bench.gui.filedialog.askopenfilename", return_value=str(target)):
            self.app.browse("prompt_file", "file")
        self.app.prompt.insert("end", " GUI edit")
        before = self.app._prompt(), self.app.variables["prompt_file"].get()
        target.write_text("external edit", encoding="utf-8")
        with patch("codex_bench.gui.filedialog.askopenfilename", return_value=""), \
             patch.object(self.app.document, "select_prompt") as select:
            self.app.browse("prompt_file", "file")
        select.assert_not_called()
        self.assertEqual((self.app._prompt(), self.app.variables["prompt_file"].get()), before)
        with patch("codex_bench.gui.messagebox.showerror") as error:
            self.assertFalse(self.app.save())
        error.assert_called_once()
        self.assertEqual(target.read_text(encoding="utf-8"), "external edit")

    def test_busy_controls_and_missing_metrics(self):
        self.app._set_busy(True)
        self.assertEqual(str(self.app.variables["mode"].get()), "sequential")
        self.assertEqual(str(self.app.stop_button["state"]), "normal")
        self.app._set_busy(False)
        self.assertEqual(str(self.app.stop_button["state"]), "disabled")
        first = self.app.result_tree.get_children()[0]
        values = self.app.result_tree.item(first, "values")
        self.assertEqual(values[4:], ("—", "—", "—", "—"))

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
