"""Check the files intended for GitHub; never calls Codex, Docker, or a model."""
from __future__ import annotations

import json
import re
import stat
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from codex_bench.config import load_config
from codex_bench.util import BenchError

MAX_FILE_BYTES = 2 * 1024 * 1024
LOCAL_DIRS = {'.git', '.tools', '.test-data', '.venv', 'venv', 'node_modules',
              '__pycache__', 'dist', 'build', '.codex', '.aws', '.ssh', '.agents'}
PATTERNS = {
    'personal home path': re.compile(r'(?i)(?:[A-Z]:[/\\]+Users[/\\]+[^/\\\s"<>]+|/(?:Users|home)/[^/\s"<>]+)'),
    'API token': re.compile(r'\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{30,})'),
    'private key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
}


def publication_files(root: Path = ROOT) -> list[Path]:
    result = subprocess.run(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
                            cwd=root, capture_output=True, check=True)
    names = set(result.stdout.decode('utf-8').split('\0')) - {''}
    return [root / name for name in sorted(names) if (root / name).exists()]


def check_file(path: Path, root: Path = ROOT) -> list[str]:
    name = path.relative_to(root).as_posix()
    problems = []
    parts = path.relative_to(root).parts
    if (any(part in LOCAL_DIRS or part.startswith('benchmark-results') for part in parts)
            or path.name.endswith('.local.json') or path.name in {'.env', '.npmrc', 'auth.json'}
            or path.name.startswith('.env.') and path.name != '.env.example'):
        problems.append(f'{name}: local/generated file must not be published')
    info = path.lstat()
    if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
        return [*problems, f'{name}: symlink/reparse point must be reviewed separately']
    if info.st_size > MAX_FILE_BYTES:
        return [*problems, f'{name}: file exceeds 2 MiB publication budget']
    try:
        source = path.read_text(encoding='utf-8-sig')
    except UnicodeError:
        return problems
    for line_number, line in enumerate(source.splitlines(), 1):
        for label, pattern in PATTERNS.items():
            if pattern.search(line):
                # Report location/type only, never echo a potential secret.
                problems.append(f'{name}:{line_number}: {label}')
    if path.suffix.lower() == '.md':
        for target in re.findall(r'(?<!!)\[[^\]]+\]\(([^)]+)\)', source):
            target = target.strip().strip('<>')
            parsed = urlsplit(target)
            if parsed.scheme or not parsed.path:
                continue
            resolved = (path.parent / unquote(parsed.path)).resolve()
            if not resolved.is_relative_to(root.resolve()) or not resolved.exists():
                problems.append(f'{name}: missing/outside document link: {target}')
    if path.name == 'config.json' or path.parent == root / 'examples' and path.name.startswith('config.'):
        try:
            raw = json.loads(source)
            if raw.get('codex_executable', 'codex') != 'codex':
                problems.append(f'{name}: public examples must use codex on PATH')
            for key in ('prompt_file', 'starter_dir', 'output_dir', 'scratch_dir'):
                value = raw.get(key)
                if value and (Path(value).is_absolute() or re.match(r'^[A-Za-z]:', value)):
                    problems.append(f'{name}: {key} must be portable')
            load_config(path)
        except (BenchError, OSError, ValueError, TypeError, KeyError) as exc:
            problems.append(f'{name}: invalid public config: {type(exc).__name__}')
    return problems


def main() -> int:
    try:
        files = publication_files()
        problems = [problem for path in files for problem in check_file(path)]
    except (OSError, subprocess.SubprocessError) as exc:
        print(f'Unable to inspect Git working copy: {exc}', file=sys.stderr)
        return 2
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    size = sum(path.stat().st_size for path in files)
    print(f'OK: {len(files)} publication files, {size / 1024:.1f} KiB; configs and document links valid.')
    print('No detected personal home paths, known token/key patterns, local settings, or generated directories.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
