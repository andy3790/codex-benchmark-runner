import json
import os
import subprocess
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_bench.codex import build_command, configuration_overrides, diagnose, toml_value, validate_execution_config, validate_execution_sandbox
from tests.helpers import DIAGNOSTICS, setup_experiment, test_directory


class ExecutionConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = test_directory()
        self.root = Path(self.temp.name) / 'project.with.dots'
        self.root.mkdir()
        self.cfg = setup_experiment(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_permissions_are_one_inline_table_with_literal_special_and_path_keys(self):
        command = build_command(self.cfg, self.cfg.jobs[0], self.root / 'work', DIAGNOSTICS)
        options = [command[i + 1] for i, value in enumerate(command) if value == '--config']
        self.assertEqual(sum(value.startswith('permissions=') for value in options), 1)
        self.assertFalse(any(value.startswith('permissions.') for value in options))
        permission = tomllib.loads(next(value for value in options if value.startswith('permissions=')))['permissions']['benchmark']
        self.assertEqual(permission['filesystem'][':workspace_roots'], {'.': 'write'})
        self.assertEqual(permission['filesystem'][str(self.root.resolve())], 'deny')
        self.assertFalse(permission['network']['enabled'])
        self.assertEqual(permission['extends'], ':workspace')

    def test_toml_encoder_roundtrips_backslashes_quotes_unicode_and_dotted_keys(self):
        source = {':workspace_roots': {'.': 'write'}, 'C:\\path.with.dots\\한글': 'deny', 'quoted"key': [True, False, 'x\\y']}
        self.assertEqual(tomllib.loads('value = ' + toml_value(source))['value'], source)
        with self.assertRaises(ValueError):
            toml_value(float('nan'))

    def test_probe_creates_no_sessions_and_does_not_use_or_copy_auth_home(self):
        observed = []
        def run(command, **kwargs):
            observed.append(command)
            self.assertNotIn('exec', command)
            self.assertNotIn('--model', command)
            if '--help' in command:
                return subprocess.CompletedProcess(command, 0, '--print-startup-config', '')
            self.assertNotEqual(kwargs['env']['CODEX_HOME'], 'existing-user-auth-home')
            self.assertEqual(list(Path(kwargs['env']['CODEX_HOME']).iterdir()), [])
            return subprocess.CompletedProcess(command, 0, '{"effective":{"private":"do-not-retain-config"}}', '')
        with patch.dict(os.environ, {'CODEX_HOME': 'existing-user-auth-home'}), patch('codex_bench.codex.subprocess.run', side_effect=run):
            result = validate_execution_config(self.cfg, DIAGNOSTICS)
            self.assertEqual(os.environ['CODEX_HOME'], 'existing-user-auth-home')
        self.assertEqual(result['exit_code'], 0)
        self.assertNotIn('do-not-retain-config', repr(result))
        self.assertEqual(len(observed), 2)
        self.assertFalse(self.cfg.output.exists())
        self.assertFalse(self.cfg.scratch.exists())

    def test_diagnosis_surfaces_native_config_error_before_any_model_execution(self):
        features = '--json --model --config --sandbox --skip-git-repo-check --ephemeral --ignore-user-config'
        def run(command, **kwargs):
            if 'exec' in command:
                self.assertIn('--help', command)
            if '--print-startup-config' in command:
                return subprocess.CompletedProcess(command, 1, json.dumps({'error': {'message': 'invalid filesystem permissions'}}), 'Error: startup configuration unavailable')
            if 'app-server' in command:
                return subprocess.CompletedProcess(command, 0, '--print-startup-config', '')
            return subprocess.CompletedProcess(command, 0, 'codex-cli 0.164.0' if '--version' in command else features, '')
        with patch('codex_bench.codex.shutil.which', return_value='codex.exe'), patch('codex_bench.codex.subprocess.run', side_effect=run):
            result = diagnose('codex', config=self.cfg)
        self.assertFalse(result['configuration_validated'])
        self.assertIn('invalid filesystem permissions', '\n'.join(result['errors']))

    def test_probe_requires_a_model_free_validation_capability(self):
        with patch('codex_bench.codex.subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'old help', '')) as run:
            result = validate_execution_config(self.cfg, DIAGNOSTICS)
        self.assertNotEqual(result['exit_code'], 0)
        self.assertIn('--print-startup-config', result['error'])
        self.assertEqual(run.call_count, 1)

    def test_probe_rejects_invalid_or_empty_native_response(self):
        values = [subprocess.CompletedProcess([], 0, '--print-startup-config', ''), subprocess.CompletedProcess([], 0, '', '')]
        with patch('codex_bench.codex.subprocess.run', side_effect=values):
            result = validate_execution_config(self.cfg, DIAGNOSTICS)
        self.assertIn('valid startup', result['error'])

    def test_probe_timeout_is_a_diagnostic_failure(self):
        with patch('codex_bench.codex.subprocess.run', side_effect=subprocess.TimeoutExpired('fixture', 15)):
            result = validate_execution_config(self.cfg, DIAGNOSTICS)
        self.assertIsNone(result['exit_code'])
        self.assertTrue(result['error'])

    def test_workspace_write_probe_uses_same_explicit_policy_as_exec(self):
        self.cfg.isolation = 'workspace-write'
        observed = []
        def run(command, **kwargs):
            if '--help' in command:
                return subprocess.CompletedProcess(command, 0, '--print-startup-config', '')
            observed.extend(command)
            return subprocess.CompletedProcess(command, 0, '{}', '')
        with patch('codex_bench.codex.subprocess.run', side_effect=run):
            self.assertEqual(validate_execution_config(self.cfg, DIAGNOSTICS)['exit_code'], 0)
        self.assertIn('sandbox_mode="workspace-write"', observed)
        self.assertFalse(any(value.startswith('permissions=') for value in observed))

    def test_windows_restricted_prefers_mxc_without_disabling_sandbox(self):
        if os.name != 'nt':
            self.skipTest('Windows-specific implementation selection')
        overrides = configuration_overrides(self.cfg)
        self.assertIs(overrides['features.prefer_mxc'], True)
        self.assertEqual(overrides['approval_policy'], 'never')
        self.assertEqual(overrides['permissions']['benchmark']['filesystem'][':root'], 'deny')
        self.cfg.isolation = 'workspace-write'
        self.assertNotIn('features.prefer_mxc', configuration_overrides(self.cfg))

    @unittest.skipUnless(os.name == 'nt', 'Windows runtime reads')
    def test_runtime_reads_are_narrow_and_cannot_expose_protected_grader(self):
        runtime = Path('C:/benchmark-runtime-fixture/pwsh').resolve()
        def which(name):
            if name == 'pwsh':
                return str(runtime / 'pwsh.exe')
            if name == 'rg':
                return str(self.cfg.grader_path / 'rg.exe')
            return None
        with patch('codex_bench.codex.shutil.which', side_effect=which):
            filesystem = configuration_overrides(self.cfg)['permissions']['benchmark']['filesystem']
        self.assertEqual(filesystem[str(runtime)], 'read')
        self.assertEqual(filesystem[str(self.cfg.grader_path)], 'deny')
        self.assertNotIn(str(runtime.parent), filesystem)

    def test_sandbox_probe_checks_canaries_without_models_or_existing_auth(self):
        def run(command, **kwargs):
            self.assertNotIn('exec', command)
            self.assertNotIn('--model', command)
            self.assertIn('sandbox', command)
            self.assertEqual(list(Path(kwargs['env']['CODEX_HOME']).iterdir()), [])
            self.assertEqual(kwargs['env']['TEMP'], str(kwargs['cwd']))
            (kwargs['cwd'] / 'probe.txt').write_text('ok')
            return subprocess.CompletedProcess(command, 0, 'BENCHMARK_SANDBOX_OK', '')
        with patch('codex_bench.codex.subprocess.run', side_effect=run):
            self.assertIsNone(validate_execution_sandbox(self.cfg, DIAGNOSTICS)['error'])

    def test_sandbox_probe_refuses_unsupported_unelevated_implementation(self):
        message = 'windows unelevated restricted-token sandbox cannot enforce split filesystem read restrictions'
        with patch('codex_bench.codex.subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', message)):
            result = validate_execution_sandbox(self.cfg, DIAGNOSTICS)
        self.assertNotEqual(result['exit_code'], 0)
        self.assertIn('cannot enforce', result['error'])

    def test_sandbox_probe_rejects_missing_workspace_write_or_denied_write_leak(self):
        for leak in (False, True):
            def run(command, **kwargs):
                if leak:
                    (kwargs['cwd'] / 'probe.txt').write_text('ok')
                    (kwargs['cwd'].parent / 'denied' / 'outside.txt').write_text('leaked')
                return subprocess.CompletedProcess(command, 0, 'BENCHMARK_SANDBOX_OK', '')
            with self.subTest(leak=leak), patch('codex_bench.codex.subprocess.run', side_effect=run):
                self.assertIn('canary check failed', validate_execution_sandbox(self.cfg, DIAGNOSTICS)['error'])

    def test_sandbox_probe_timeout_prevents_execution(self):
        with patch('codex_bench.codex.subprocess.run', side_effect=subprocess.TimeoutExpired('fixture', 35)):
            result = validate_execution_sandbox(self.cfg, DIAGNOSTICS)
        self.assertIsNone(result['exit_code'])
        self.assertTrue(result['error'])

    @unittest.skipUnless(os.name == 'nt', 'Windows sandbox diagnosis')
    def test_diagnosis_stops_before_model_when_sandbox_cannot_start(self):
        features = '--json --model --config --sandbox --skip-git-repo-check --ephemeral --ignore-user-config'
        with patch('codex_bench.codex.shutil.which', return_value='codex.exe'), \
             patch('codex_bench.codex.subprocess.run', return_value=subprocess.CompletedProcess([], 0, features, '')), \
             patch('codex_bench.codex.validate_execution_config', return_value={'exit_code': 0, 'error': None}), \
             patch('codex_bench.codex.validate_execution_sandbox', return_value={'exit_code': 1, 'error': 'cannot enforce denied reads'}):
            result = diagnose('codex', config=self.cfg)
        self.assertTrue(result['configuration_validated'])
        self.assertFalse(result['sandbox_validated'])
        self.assertIn('no model called', '\n'.join(result['errors']))
