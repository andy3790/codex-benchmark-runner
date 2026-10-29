import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_bench.cli import main
from codex_bench.config import default_config_path, load_config
from codex_bench.util import BenchError
from tests.helpers import test_directory

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('repository_check', ROOT / 'tools' / 'check_repository.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class ProjectLayoutTests(unittest.TestCase):
    def test_local_settings_preferred_and_public_fallback_kept(self):
        with test_directory() as directory:
            root = Path(directory)
            public = root / 'config.json'
            public.write_text('{}')
            self.assertEqual(default_config_path(root), public)
            local = root / 'config.local.json'
            local.write_text('{}')
            self.assertEqual(default_config_path(root), local)

    def test_explicit_cli_config_overrides_local_auto_selection_without_calls(self):
        explicit = Path('chosen-config.json')
        with patch('codex_bench.cli.default_config_path') as select, \
             patch('codex_bench.cli.load_config', side_effect=BenchError('selection test')) as read, \
             patch('codex_bench.cli.diagnose') as diagnose, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['--config', str(explicit), '--doctor']), 2)
        read.assert_called_once_with(explicit)
        select.assert_not_called()
        diagnose.assert_not_called()

    def test_cli_default_uses_local_selection_without_model_calls(self):
        local = Path('config.local.json')
        with patch('codex_bench.cli.default_config_path', return_value=local), \
             patch('codex_bench.cli.load_config', side_effect=BenchError('selection test')) as read, \
             patch('codex_bench.cli.diagnose') as diagnose, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['--doctor']), 2)
        read.assert_called_once_with(local)
        diagnose.assert_not_called()

    def test_public_configs_load_from_their_own_folder(self):
        examples = [ROOT / 'config.json', *sorted((ROOT / 'examples').glob('config.*.json'))]
        self.assertEqual(len(examples), 3)
        for path in examples:
            with self.subTest(path=path.name):
                cfg = load_config(path)
                self.assertEqual(cfg.codex, 'codex')
                self.assertTrue(cfg.prompt_path.is_file())
                self.assertTrue((cfg.grader_path / 'plugin.py').is_file())
                self.assertFalse(checker.check_file(path))

    def test_repository_check_detects_private_data_without_printing_values(self):
        with test_directory() as directory:
            root = Path(directory)
            path = root / 'config.local.json'
            key = 'sk-' + 'a' * 30
            fictional_home = '/'.join(('C:', 'Users', 'private-person', 'project'))
            path.write_text(json.dumps({'path': fictional_home, 'token': key}))
            errors = checker.check_file(path, root)
            self.assertTrue(any('local/generated' in error for error in errors))
            self.assertTrue(any('personal home path' in error for error in errors))
            self.assertTrue(any('API token' in error for error in errors))
            self.assertNotIn(key, '\n'.join(errors))
            self.assertNotIn('private-person', '\n'.join(errors))

    def test_repository_check_rejects_broken_markdown_links(self):
        with test_directory() as directory:
            root = Path(directory)
            path = root / 'README.md'
            path.write_text('[missing](missing.md) [website](https://example.com)')
            self.assertEqual(len(checker.check_file(path, root)), 1)
            (root / 'missing.md').write_text('present')
            self.assertFalse(checker.check_file(path, root))
