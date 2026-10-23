import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from codex_bench.process import run_process


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def run_python(self, code, **kwargs):
        return run_process([sys.executable, "-I", "-c", code], cwd=self.root,
                           stdout=self.root / "out", stderr=self.root / "err", timeout=kwargs.pop("timeout", 3), **kwargs)

    def test_stdin_stdout_stderr_and_exit(self):
        process = self.run_python("import sys; print(sys.stdin.read()); print('err',file=sys.stderr); sys.exit(3)", input_bytes="한글".encode())
        self.assertEqual(process.exit_code, 3)
        self.assertIsNone(process.termination)
        self.assertIn("err", (self.root / "err").read_text())

    def test_timeout(self):
        process = self.run_python("import time; time.sleep(60)", timeout=0.2)
        self.assertEqual(process.termination, "timeout")
        self.assertLess(process.duration_seconds, 5)

    def test_cancel(self):
        cancel = threading.Event()
        timer = threading.Timer(0.2, cancel.set)
        timer.start()
        try:
            process = self.run_python("import time; time.sleep(60)", cancel=cancel)
        finally:
            timer.join()
        self.assertEqual(process.termination, "interrupted")

    def test_output_bound(self):
        process = self.run_python("import sys; sys.stdout.write('x'*100000); sys.stdout.flush()", max_output_bytes=1000)
        self.assertEqual(process.termination, "output_limit")

    def test_launch_error_is_preserved(self):
        process = run_process([str(self.root / "missing.exe")], cwd=self.root, stdout=self.root / "out", stderr=self.root / "err", timeout=1)
        self.assertEqual(process.termination, "launch_error")
        self.assertIsNone(process.exit_code)
        self.assertTrue(process.error)

    def test_descendant_stopped_even_if_parent_exits(self):
        sentinel = self.root / "should-not-exist"
        child_code = f"import time; from pathlib import Path; time.sleep(1.5); Path({str(sentinel)!r}).write_text('leaked')"
        parent_code = f"import subprocess,sys; subprocess.Popen([sys.executable,'-I','-c',{child_code!r}])"
        process = self.run_python(parent_code)
        self.assertEqual(process.exit_code, 0)
        time.sleep(1.8)
        self.assertFalse(sentinel.exists(), "A descendant escaped process-tree cleanup")
