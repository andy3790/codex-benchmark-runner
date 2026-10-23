import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_bench.codex import build_command, classify_failure, diagnose
from tests.helpers import DIAGNOSTICS, setup_experiment


class CodexTests(unittest.TestCase):
    def test_command_is_safe_and_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = setup_experiment(Path(directory))
            command = build_command(cfg, cfg.jobs[0], Path(directory) / "project", DIAGNOSTICS)
            self.assertIn("--json", command)
            self.assertIn('permissions.benchmark.extends=":workspace"', command)
            self.assertIn('permissions.benchmark.filesystem.":root"="deny"', command)
            self.assertNotIn("--sandbox", command)
            self.assertIn('approval_policy="never"', command)
            self.assertIn('model_reasoning_effort="low"', command)
            self.assertIn("--ephemeral", command)
            self.assertIn("--ignore-user-config", command)
            self.assertEqual(command[-1], "-")
            self.assertFalse(any(value in command for value in ["--yolo", "danger-full-access", "resume", "--full-auto", "--add-dir"]))

    def test_legacy_workspace_write_requires_explicit_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = setup_experiment(Path(directory), isolation="workspace-write")
            command = build_command(cfg, cfg.jobs[0], Path(directory) / "project", DIAGNOSTICS)
            self.assertIn("--sandbox", command)
            self.assertIn("workspace-write", command)
            self.assertFalse(any(v.startswith("default_permissions=") for v in command))

    def test_diagnosis_only_help_and_version(self):
        import subprocess
        help_text = "--json --model --config --sandbox --skip-git-repo-check --ephemeral --ignore-user-config --no-daemon"
        with patch("codex_bench.codex.shutil.which", return_value="codex.exe"), patch("codex_bench.codex.subprocess.run", return_value=subprocess.CompletedProcess([], 0, help_text, "")) as run:
            result = diagnose("codex")
        self.assertFalse(result["errors"])
        self.assertEqual(run.call_count, 3)
        for call in run.call_args_list:
            self.assertTrue("--help" in call.args[0] or "--version" in call.args[0])

    def test_missing_capability_fails_closed(self):
        import subprocess
        with patch("codex_bench.codex.shutil.which", return_value="codex.exe"), patch("codex_bench.codex.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "old", "")):
            self.assertTrue(diagnose("codex")["errors"])

    def test_no_executable(self):
        with patch("codex_bench.codex.shutil.which", return_value=None):
            self.assertTrue(diagnose("codex")["errors"])

    def test_failure_classification(self):
        parsed = {"completed_event": True, "errors": []}
        self.assertIsNone(classify_failure(0, parsed, "", None))
        self.assertEqual(classify_failure(1, parsed, "reasoning effort not supported", None), "unsupported_combination")
        self.assertEqual(classify_failure(1, parsed, "401 unauthorized", None), "authentication")
        self.assertEqual(classify_failure(1, parsed, "rate limit", None), "rate_limit")
        self.assertEqual(classify_failure(0, {}, "", None), "incomplete_events")
