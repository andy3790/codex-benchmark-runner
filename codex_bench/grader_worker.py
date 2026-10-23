"""Run only a USER-TRUSTED plugin, never import the candidate in this process."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# -I excludes cwd/PYTHONPATH. This is the trusted installation path, not submission.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codex_bench.grading import GradeContext, validate_grade
from codex_bench.util import read_json, write_json


def main():
    request = read_json(Path(sys.argv[1]))
    plugin_dir = Path(request["plugin_dir"])
    spec = importlib.util.spec_from_file_location("benchmark_trusted_grader", plugin_dir / "plugin.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    context = GradeContext(Path(request["artifact_dir"]), plugin_dir,
                           request["test_timeout_seconds"], request["options"])
    result = validate_grade(module.grade(Path(request["submission"]), context))
    write_json(Path(request["result_file"]), result)


if __name__ == "__main__":
    main()
