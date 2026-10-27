import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_bench.codex import diagnose, execution_environment, resolve_executable
from codex_bench.util import BenchError
from tests.helpers import test_directory


class CodexResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = test_directory()
        self.root = Path(self.temp.name)
        self.shim = self.root / 'npm' / 'codex.cmd'
        self.shim.parent.mkdir()
        self.shim.write_text('@echo off\nREM never execute this test wrapper\n')
        self.package = self.shim.parent / 'node_modules' / '@openai' / 'codex'
        self.package.mkdir(parents=True)
        (self.package / 'package.json').write_text(json.dumps({'name': '@openai/codex', 'version': '0.164.0'}))

    def tearDown(self):
        self.temp.cleanup()

    def binary(self, layout='nested', arch='x64', folder='bin'):
        vendor = {'nested': self.package / 'node_modules' / '@openai' / ('codex-win32-' + arch) / 'vendor',
                  'hoisted': self.package.parent / ('codex-win32-' + arch) / 'vendor',
                  'legacy': self.package / 'vendor'}[layout]
        triple = ('x86_64' if arch == 'x64' else 'aarch64') + '-pc-windows-msvc'
        path = vendor / triple / folder / 'codex.exe'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'trusted fixture filename; never executed')
        return path

    def resolve(self, path=None, arch='AMD64'):
        with patch('codex_bench.codex.shutil.which', return_value=str(path or self.shim)), patch('codex_bench.codex.platform.machine', return_value=arch):
            return resolve_executable('codex')

    def test_global_npm_nested_native_package(self):
        native = self.binary()
        result = self.resolve()
        self.assertEqual(result['executable'], str(native.resolve()))
        self.assertEqual(result['resolved_from'], str(self.shim))
        self.assertEqual(result['resolution'], 'npm-native')
        self.assertEqual(result['npm_package_version'], '0.164.0')

    def test_hoisted_optional_package(self):
        native = self.binary('hoisted')
        self.assertEqual(self.resolve()['executable'], str(native.resolve()))

    def test_legacy_vendor_codex_layout(self):
        native = self.binary('legacy', folder='codex')
        self.assertEqual(self.resolve()['executable'], str(native.resolve()))

    def test_arm64_layout(self):
        native = self.binary(arch='arm64')
        self.assertEqual(self.resolve(arch='ARM64')['executable'], str(native.resolve()))

    def test_powershell_shim_resolves_without_running_powershell(self):
        native = self.binary()
        self.assertEqual(self.resolve(self.shim.with_suffix('.ps1'))['executable'], str(native.resolve()))

    def test_explicit_npm_js_entry(self):
        native = self.binary()
        entry = self.package / 'bin' / 'codex.js'
        entry.parent.mkdir()
        entry.write_text('// trusted fixture; never executed')
        self.assertEqual(self.resolve(entry)['executable'], str(native.resolve()))

    def test_project_local_node_modules_bin(self):
        self.binary()
        shim = self.package.parent.parent / '.bin' / 'codex.cmd'
        shim.parent.mkdir()
        shim.write_text('REM fixture')
        self.assertEqual(self.resolve(shim)['npm_package_root'], str(self.package.resolve()))

    def test_missing_optional_dependency_has_actionable_error(self):
        with self.assertRaisesRegex(BenchError, '--include=optional'):
            self.resolve()

    def test_wrong_package_name_is_not_used(self):
        self.binary()
        (self.package / 'package.json').write_text('{"name":"different-package"}')
        with self.assertRaisesRegex(BenchError, 'Unrecognized'):
            self.resolve()

    def test_unrelated_or_unrecognized_wrapper_is_not_executed(self):
        with self.assertRaisesRegex(BenchError, 'Unsupported shell wrapper'):
            self.resolve(self.shim.with_name('custom.bat'))
        with self.assertRaisesRegex(BenchError, 'Cannot locate'):
            self.resolve(self.root / 'other' / 'codex.cmd')

    def test_direct_native_selection_stays_unchanged(self):
        path = self.root / 'standalone' / 'codex.exe'
        self.assertEqual(self.resolve(path)['executable'], str(path))

    def test_diagnosis_and_generated_command_use_native_path(self):
        native = self.binary()
        help_text = '--json --model --config --sandbox --skip-git-repo-check --ephemeral --ignore-user-config'
        def output(argv, **kwargs):
            self.assertEqual(argv[0], str(native.resolve()))
            self.assertNotIn('.cmd', argv[0])
            self.assertEqual(kwargs['env']['CODEX_MANAGED_PACKAGE_ROOT'], str(self.package.resolve()))
            return subprocess.CompletedProcess(argv, 0, 'codex-cli 0.164.0' if '--version' in argv else help_text, '')
        with patch('codex_bench.codex.shutil.which', return_value=str(self.shim)), patch('codex_bench.codex.platform.machine', return_value='AMD64'), patch('codex_bench.codex.subprocess.run', side_effect=output) as run:
            result = diagnose('codex')
        self.assertFalse(result['errors'])
        self.assertEqual(run.call_count, 3)
        from codex_bench.codex import build_command
        from tests.helpers import setup_experiment
        cfg = setup_experiment(self.root)
        self.assertEqual(build_command(cfg, cfg.jobs[0], self.root / 'work', result)[0], str(native.resolve()))

    def test_auth_environment_is_preserved_and_npm_metadata_is_forwarded(self):
        native = self.binary('legacy')
        sidecar = native.parent.parent / 'path'
        sidecar.mkdir()
        result = self.resolve()
        base = {'CODEX_HOME': 'existing-auth-home', 'Path': 'original-path', 'CODEX_MANAGED_BY_BUN': '1'}
        env = execution_environment(result, base)
        self.assertEqual(env['CODEX_HOME'], 'existing-auth-home')
        self.assertEqual(env['CODEX_MANAGED_BY_NPM'], '1')
        self.assertNotIn('CODEX_MANAGED_BY_BUN', env)
        self.assertEqual(env['Path'], str(sidecar.resolve()) + os.pathsep + 'original-path')
        self.assertEqual(base['Path'], 'original-path')
