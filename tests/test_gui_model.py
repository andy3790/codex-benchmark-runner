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

    def test_selected_prompt_external_change_or_deletion_blocks_save(self):
        target = self.root / "selected.md"
        config_before = self.cfg.path.read_bytes()
        original_before = self.cfg.prompt_path.read_bytes()
        self.values["prompt_file"] = str(target)
        for deleted in (False, True):
            with self.subTest(deleted=deleted):
                target.write_text("selected content", encoding="utf-8")
                self.document.select_prompt(target)
                if deleted:
                    target.unlink()
                else:
                    target.write_text("external edit", encoding="utf-8")
                with self.assertRaises(BenchError):
                    self.document.save(self.values, "GUI edit")
                self.assertEqual(self.cfg.path.read_bytes(), config_before)
                self.assertEqual(self.cfg.prompt_path.read_bytes(), original_before)
                if deleted:
                    self.assertFalse(target.exists())
                else:
                    self.assertEqual(target.read_text(encoding="utf-8"), "external edit")

    def test_selected_prompt_can_be_edited_saved_and_saved_again(self):
        target = self.root / "selected.md"
        target.write_text("선택한 프롬프트\n", encoding="utf-8")
        original_before = self.cfg.prompt_path.read_bytes()
        self.values["prompt_file"] = "selected.md"
        prompt = self.document.select_prompt(Path("selected.md")) + "수정한 내용\n"
        saved = self.document.save(self.values, prompt)
        self.assertEqual(saved.prompt_path, target)
        self.assertEqual(target.read_bytes(), prompt.encode("utf-8"))
        self.assertEqual(self.cfg.prompt_path.read_bytes(), original_before)
        config_before, prompt_before = self.cfg.path.read_bytes(), target.read_bytes()
        fingerprint = saved.fingerprint
        repeated = self.document.save(self.document.original_form, prompt)
        self.assertEqual(repeated.fingerprint, fingerprint)
        self.assertEqual(self.cfg.path.read_bytes(), config_before)
        self.assertEqual(target.read_bytes(), prompt_before)

    def test_selected_prompt_noop_preserves_bom_and_crlf(self):
        target = self.root / "selected.md"
        content = "새 프롬프트\r\n두 번째 줄\r\n".encode("utf-8-sig")
        target.write_bytes(content)
        self.values["prompt_file"] = str(target)
        prompt = self.document.select_prompt(target)
        self.assertEqual(prompt, "새 프롬프트\n두 번째 줄\n")
        self.document.save(self.values, prompt)
        self.assertEqual(target.read_bytes(), content)
        self.document.save(self.document.original_form, prompt)
        self.assertEqual(target.read_bytes(), content)

    def test_original_prompt_change_still_blocks_a_different_selected_prompt(self):
        target = self.root / "selected.md"
        target.write_text("selected content", encoding="utf-8")
        self.values["prompt_file"] = str(target)
        self.document.select_prompt(target)
        self.cfg.prompt_path.write_text("external original edit", encoding="utf-8")
        with self.assertRaises(BenchError):
            self.document.save(self.values, "GUI edit")
        self.assertEqual(target.read_text(encoding="utf-8"), "selected content")
        self.assertEqual(self.cfg.prompt_path.read_text(encoding="utf-8"), "external original edit")

    def test_failed_prompt_selection_preserves_previous_baseline(self):
        target = self.root / "selected.md"
        target.write_text("selected content", encoding="utf-8")
        self.values["prompt_file"] = str(target)
        self.document.select_prompt(target)
        with self.assertRaises(OSError):
            self.document.select_prompt(self.root / "missing.md")
        target.write_text("external edit", encoding="utf-8")
        with self.assertRaises(BenchError):
            self.document.save(self.values, "GUI edit")
        self.assertEqual(target.read_text(encoding="utf-8"), "external edit")

    def test_new_prompt_path_can_still_be_created(self):
        target = self.root / "new" / "prompt.md"
        self.values["prompt_file"] = str(target)
        saved = self.document.save(self.values, "new prompt\n")
        self.assertEqual(saved.prompt_path, target)
        self.assertEqual(target.read_bytes(), b"new prompt\n")

    def test_existing_prompt_requires_a_selection_baseline(self):
        target = self.root / "unselected.md"
        target.write_text("existing content", encoding="utf-8")
        config_before = self.cfg.path.read_bytes()
        self.values["prompt_file"] = str(target)
        with self.assertRaises(BenchError):
            self.document.save(self.values, "GUI edit")
        self.assertEqual(target.read_text(encoding="utf-8"), "existing content")
        self.assertEqual(self.cfg.path.read_bytes(), config_before)

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
