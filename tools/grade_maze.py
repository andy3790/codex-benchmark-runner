"""Grade saved maze submissions without running Codex. Candidates stay in Docker."""
from __future__ import annotations

import argparse
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from codex_bench.config import load_config
from codex_bench.report import write_reports
from codex_bench.runner import OutputLock, clean_containers, grade_submission, prepare
from codex_bench.util import BenchError, copy_tree, is_within, tree_hash, utc_now, write_json


def main(argv=None):
    parser = argparse.ArgumentParser(description='Independently grade a saved maze project; never calls a model')
    parser.add_argument('submission', type=Path, help='Directory containing index.html and maze-core.js')
    parser.add_argument('--output', type=Path, help='New/empty grading output directory')
    parser.add_argument('--image', default='node:22-bookworm-slim', help='Pre-pulled Docker Linux image containing Node')
    parser.add_argument('--test-timeout', type=float, default=5)
    parser.add_argument('--timeout', type=float, default=120)
    args = parser.parse_args(argv)
    source = args.submission.resolve()
    output = (args.output or ROOT / 'benchmark-results' / ('maze-grade-' + uuid.uuid4().hex[:12])).resolve()
    try:
        if not source.is_dir():
            raise BenchError(f'Submission directory not found: {source}')
        if is_within(output, source) or is_within(source, output):
            raise BenchError('Submission and grading output must not overlap')
        raw = {'models': ['saved-submission'], 'reasoning_efforts': ['low'],
               'prompt_file': str(ROOT / 'maze_prompt.md'), 'starter_dir': str(ROOT / 'starter'),
               'output_dir': str(output), 'grader': {'plugin_dir': str(ROOT / 'graders' / 'maze'),
                   'test_timeout_seconds': args.test_timeout, 'timeout_seconds': args.timeout,
                   'options': {'docker_image': args.image}}}
        cfg = load_config(output / 'config.json', raw_override=raw)
        with OutputLock(output):
            # A grading-only run is intentionally never resumed over an existing snapshot.
            if (output / 'manifest.json').exists():
                raise BenchError('Choose a new empty --output directory to preserve prior grading evidence')
            manifest = prepare(cfg)
            manifest.update({'grading_only': True, 'real_model_calls': 0, 'source_submission': str(source)})
            job = cfg.jobs[0]
            attempt_id = uuid.uuid4().hex
            attempt = output / 'runs' / job.id / 'attempts' / attempt_id
            attempt.mkdir(parents=True)
            copy_tree(source, attempt / 'submission', cfg.max_project_bytes)
            started, timer = utc_now(), time.monotonic()
            code = 0
            try:
                grade, error, duration = grade_submission(cfg, attempt, threading.Event(), timer + args.timeout)
            except KeyboardInterrupt:
                grade, error, duration, code = None, 'interrupted', time.monotonic() - timer, 130
            finally:
                clean_containers(attempt / 'grading')
            row = {**job.as_dict(), 'model': 'saved-submission (grading only)', 'effort': 'not applicable',
                   'attempt_id': attempt_id, 'started_at': started, 'ended_at': utc_now(),
                   'status': 'interrupted' if code == 130 else 'failed' if error else 'succeeded',
                   'success': not error, 'usage': {}, 'grade': grade, 'grading_error': error,
                   'generation_seconds': None, 'grading_seconds': duration, 'duration_seconds': duration,
                   'artifact_path': str(attempt.relative_to(output)).replace('\\', '/'),
                   'submission_hashes': tree_hash(attempt / 'submission'),
                   'warnings': ['Grading only: no model was called. Browser UI tests remain unmeasured.']}
            for filename in ['stdout.log', 'stderr.log', 'events.jsonl']:
                (attempt / filename).write_text('', encoding='utf-8')
            (attempt / 'final_message.md').write_text('Independent grading of saved code; no model generation. See grading/ for actual execution logs.\n', encoding='utf-8')
            write_json(attempt / 'result.json', row)
            write_json(output / 'runs' / job.id / 'result.json', row)
            manifest.update({'active_seconds': time.monotonic() - timer, 'last_finished_at': utc_now()})
            write_json(output / 'manifest.json', manifest)
            write_reports(output, [row], manifest)
            print(f'Report: {output / "report.html"}')
            if grade:
                print(f'Score: {grade["score"]}/{grade["max_score"]}; planned={grade["planned_max_score"]}; skipped={grade["skipped_tests"]}')
            if error:
                print(f'Grading error: {error}', file=sys.stderr)
            return code or (2 if error else 1 if any(test['status'] in {'failed', 'error'} for test in grade['tests']) else 0)
    except (BenchError, OSError, ValueError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
