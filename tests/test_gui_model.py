import copy
import unittest
from pathlib import Path

from codex_bench.gui_model import ConfigDocument
from codex_bench.util import BenchError, read_json, write_json
from tests.helpers import setup_experiment, test_directory


class GuiModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = test_directory()
        self.root = Path(self.temp.name)
        self.cfg = setup_experiment(self.root)
        self.document = ConfigDocument.open(self.cfg.path)
        self.values = copy.deepcopy(self.document.original_form)

    def tearDown(self):
        self.temp.cleanup()

    def test_noop_save_preserves_raw_and_resume_fingerprint(self):
        config_bytes = self.cfg.path.read_bytes()
        prompt_bytes = self.cfg.prompt_path.read_bytes()
        saved = self.document.save(self.values, self.document.original_prompt)
        self.assertEqual(saved.raw, self.cfg.raw)
        self.assertEqual(saved.fingerprint, self.cfg.fingerprint)
        self.assertEqual(self.cfg.path.read_bytes(), config_bytes)
        self.assertEqual(self.cfg.prompt_path.read_bytes(), prompt_bytes)

    def test_preview_uses_unsaved_prompt_without_writes(self):
        before = self.cfg.prompt_path.read_bytes()
        self.values["repetitions"] = "2"
        cfg = self.document.build(self.values, "편집 중인 프롬프트\n")
        self.assertEqual(len(cfg.jobs), 4)
        self.assertEqual(cfg.prompt, "편집 중인 프롬프트\n")
        self.assertEqual(self.cfg.prompt_path.read_bytes(), before)
        self.assertEqual(read_json(self.cfg.path)["repetitions"], 1)

    def test_invalid_form_does_not_change_files(self):
        before = self.cfg.path.read_bytes()
        self.values["max_workers"] = "not-a-number"
        with self.assertRaises(BenchError):
            self.document.save(self.values, "edited")
        self.assertEqual(before, self.cfg.path.read_bytes())
        self.assertEqual(self.cfg.prompt, self.document.original_prompt)

    def test_external_config_change_is_not_overwritten(self):
        raw = read_json(self.cfg.path)
        raw["repetitions"] = 3
        write_json(self.cfg.path, raw)
        with self.assertRaises(BenchError):
            self.document.save(self.values, "edited")
        self.assertEqual(read_json(self.cfg.path)["repetitions"], 3)

    def test_external_prompt_change_is_not_overwritten(self):
        self.cfg.prompt_path.write_text("external", encoding="utf-8")
        with self.assertRaises(BenchError):
            self.document.save(self.values, "edited")
        self.assertEqual(self.cfg.prompt_path.read_text(), "external")

    def test_preserves_custom_grader_options_and_default_omissions(self):
        raw = read_json(self.cfg.path)
        raw["grader"]["options"] = {"custom": [1, 2], "docker_image": "python:3.12-slim"}
        write_json(self.cfg.path, raw)
        doc = ConfigDocument.open(self.cfg.path)
        values = copy.deepcopy(doc.original_form)
        values["run_timeout_seconds"] = "20"
        saved = doc.save(values, doc.original_prompt)
        self.assertEqual(saved.raw["grader"]["options"]["custom"], [1, 2])
        self.assertNotIn("max_output_bytes", saved.raw)
        self.assertEqual(saved.run_timeout, 20)

    def test_save_prompt_and_changed_settings_then_repeat_noop(self):
        self.values["mode"] = "parallel"
        self.values["max_workers"] = "2"
        prompt = "공통 프롬프트를 수정했습니다\n"
        saved = self.document.save(self.values, prompt)
        self.assertEqual(saved.workers, 2)
        self.assertEqual(saved.prompt, prompt)
        self.assertEqual(self.document.save(self.document.original_form, prompt).fingerprint, saved.fingerprint)

    def test_config_cannot_be_used_as_prompt(self):
        self.values["prompt_file"] = str(self.cfg.path)
        with self.assertRaises(BenchError):
            self.document.build(self.values, "accidental overwrite")

    def test_string_model_input_is_preserved_when_unedited(self):
        raw = read_json(self.cfg.path)
        raw["models"] = ["mock-model"]
        write_json(self.cfg.path, raw)
        doc = ConfigDocument.open(self.cfg.path)
        self.assertEqual(doc.save(doc.original_form, doc.original_prompt).raw["models"], ["mock-model"])
