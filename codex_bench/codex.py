from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import Config, Job
from .util import BenchError, is_within, utc_now


def resolve_executable(executable: str) -> dict:
    """Resolve Windows npm shims to their own package's native CLI, without a shell.

    Do not silently fall back to another Codex installation later on PATH: that
    could benchmark a different CLI version than the user selected.
    """
    resolved = shutil.which(executable)
    if not resolved:
        raise BenchError(f"Codex executable not found: {executable}")
    path = Path(resolved)
    info = {"executable": resolved, "resolved_from": resolved, "resolution": "direct"}
    wrapper = path.suffix.lower() in {".cmd", ".bat", ".ps1", ".js"}
    wrapper |= os.name == 'nt' and path.name.lower() == 'codex'
    if not wrapper:
        return info
    if path.stem.lower() != 'codex':
        raise BenchError(f"Unsupported shell wrapper: {path}. Select codex, an npm codex shim, or codex.exe.")
    if path.name.lower() == 'codex.js' and path.parent.name.lower() == 'bin':
        package = path.parent.parent
    elif path.parent.name == '.bin':
        package = path.parent.parent / '@openai' / 'codex'
    else:
        package = path.parent / 'node_modules' / '@openai' / 'codex'
    package = package.resolve()
    try:
        metadata = json.loads((package / 'package.json').read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as exc:
        raise BenchError(f"Cannot locate the npm @openai/codex package for {path}. Select its native codex.exe in the GUI, or reinstall npm install -g @openai/codex --include=optional.") from exc
    if not isinstance(metadata, dict) or metadata.get('name') != '@openai/codex':
        raise BenchError(f"Unrecognized npm package for {path}; expected @openai/codex")
    machine = platform.machine().lower()
    targets = {'amd64': ('x64', 'x86_64'), 'x86_64': ('x64', 'x86_64'), 'x64': ('x64', 'x86_64'),
               'arm64': ('arm64', 'aarch64'), 'aarch64': ('arm64', 'aarch64')}
    if machine not in targets:
        raise BenchError(f"Unsupported Windows architecture for npm Codex: {machine}. Select the native executable explicitly.")
    arch, cpu = targets[machine]
    platform_name = 'codex-win32-' + arch
    triple = cpu + '-pc-windows-msvc'
    # npm optional packages may be nested (current npm) or hoisted. Older
    # releases bundled vendor/ directly in @openai/codex.
    vendors = [package / 'node_modules' / '@openai' / platform_name / 'vendor',
               package.parent / platform_name / 'vendor', package / 'vendor']
    for vendor in vendors:
        for folder in ('bin', 'codex'):
            native = vendor / triple / folder / 'codex.exe'
            if native.is_file():
                info.update(executable=str(native.resolve()), resolution='npm-native',
                            npm_package_root=str(package), npm_package_version=metadata.get('version'),
                            environment={'CODEX_MANAGED_PACKAGE_ROOT': str(package), 'CODEX_MANAGED_BY_NPM': '1'})
                sidecar = vendor / triple / 'path'
                if sidecar.is_dir():
                    info['prepend_path'] = [str(sidecar.resolve())]
                return info
    raise BenchError(f"npm Codex native binary is missing for {triple}. Reinstall with npm install -g @openai/codex --include=optional, or select an existing native codex.exe. Package: {package}")


def execution_environment(diagnostics: dict, base: dict | None = None) -> dict:
    env = dict(os.environ if base is None else base)
    if diagnostics.get('resolution') == 'npm-native':
        for key in ('CODEX_MANAGED_BY_NPM', 'CODEX_MANAGED_BY_PNPM', 'CODEX_MANAGED_BY_BUN', 'CODEX_MANAGED_BY_VITE_PLUS'):
            env.pop(key, None)
    env.update(diagnostics.get('environment', {}))
    if diagnostics.get('prepend_path'):
        key = next((key for key in env if key.upper() == 'PATH'), 'PATH')
        env[key] = os.pathsep.join([*diagnostics['prepend_path'], env.get(key, '')])
    return env


def diagnose(executable: str, *, config: Config | None = None) -> dict:
    result = {"checked_at": utc_now(), "requested_executable": executable, "executable": None, "version": None,
              "checks": {}, "errors": [], "warnings": []}
    try:
        result.update(resolve_executable(executable))
    except BenchError as exc:
        result['errors'].append(str(exc))
        return result
    resolved = result['executable']
    for name, arguments in [("version", ["--version"]), ("root_help", ["--help"]), ("exec_help", ["exec", "--help"])]:
        try:
            proc = subprocess.run([resolved, *arguments], stdin=subprocess.DEVNULL, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=15,
                                  env=execution_environment(result),
                                  creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            result["checks"][name] = {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
            if proc.returncode:
                result["errors"].append(f"codex {arguments} exited {proc.returncode}")
            if name == "version":
                result["version"] = proc.stdout.strip()
            if proc.stderr.strip():
                result["warnings"].append(proc.stderr.strip())
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["errors"].append(str(exc))
    help_text = result["checks"].get("exec_help", {}).get("stdout", "")
    for required in ["--json", "--model", "--config", "--sandbox", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config"]:
        if required not in help_text:
            result["errors"].append(f"Required safe/reproducible CLI capability missing: {required}")
    result["no_daemon"] = "--no-daemon" in result["checks"].get("root_help", {}).get("stdout", "")
    version = re.search(r"(\d+)\.(\d+)\.(\d+)", result.get("version") or "")
    result["permission_profiles"] = bool(version and tuple(map(int, version.groups())) >= (0, 138, 0))
    if config is not None and not result['errors']:
        check = validate_execution_config(config, result)
        result['checks']['execution_config'] = check
        result['configuration_validated'] = check['exit_code'] == 0 and not check.get('error')
        if not result['configuration_validated']:
            result['errors'].append('Execution configuration validation failed: ' + check.get('error', 'unknown error'))
        elif os.name == 'nt' and config.isolation == 'restricted':
            check = validate_execution_sandbox(config, result)
            result['checks']['execution_sandbox'] = check
            result['sandbox_validated'] = check['exit_code'] == 0 and not check.get('error')
            if not result['sandbox_validated']:
                result['errors'].append('Windows sandbox validation failed (no model called): ' +
                                        check.get('error', 'unknown error'))
    return result


def toml_value(value) -> str:
    """Serialize a complete override value; quoted path keys stay inside TOML.

    Codex splits the override KEY on dots, without parsing quoted TOML keys.
    Passing filesystem.":workspace_roots"."." as a CLI key corrupts its shape.
    """
    if isinstance(value, dict):
        return '{' + ', '.join(json.dumps(str(key), ensure_ascii=False) + ' = ' + toml_value(item)
                               for key, item in value.items()) + '}'
    if isinstance(value, list):
        return '[' + ', '.join(toml_value(item) for item in value) + ']'
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def configuration_overrides(cfg: Config) -> dict:
    overrides = {"approval_policy": "never", "web_search": "disabled"}
    if cfg.isolation == "restricted":
        if os.name == 'nt':
            # exec ignores user settings for reproducibility. Explicitly prefer the
            # native implementation that supports denied reads; never relax policy.
            overrides['features.prefer_mxc'] = True
        # --sandbox would override permission profiles. Inherit the workspace policy
        # instead, retaining its protected paths while denying reads outside the run.
        filesystem = {':root': 'deny', ':minimal': 'read', ':tmpdir': 'write',
                      ':slash_tmp': 'deny', ':workspace_roots': {'.': 'write'}}
        # Exact denies also carve grading/results/source out of any broad runtime allow.
        protected = {cfg.output, cfg.grader_path, cfg.path.parent, Path(__file__).resolve().parent.parent}
        if os.name == 'nt':
            # :minimal covers OS files, but user-installed PowerShell/Python and
            # the desktop app's rg can live elsewhere. Permit their installation
            # directories read-only, never a protected benchmark directory.
            for name in ('pwsh', 'powershell', 'node', 'python', 'rg'):
                executable = shutil.which(name)
                if executable:
                    runtime = Path(executable).resolve().parent
                    if not any(is_within(runtime, path) for path in protected):
                        filesystem[str(runtime)] = 'read'
        for path in sorted(protected, key=str):
            filesystem[str(path)] = 'deny'
        overrides.update({'default_permissions': 'benchmark', 'permissions': {
            'benchmark': {'extends': ':workspace', 'filesystem': filesystem, 'network': {'enabled': False}}}})
    else:
        overrides.update({"sandbox_workspace_write.network_access": False,
                          "sandbox_workspace_write.writable_roots": [],
                          "sandbox_workspace_write.exclude_tmpdir_env_var": True,
                          "sandbox_workspace_write.exclude_slash_tmp": True})
    return overrides


def override_arguments(overrides: dict) -> list[str]:
    arguments = []
    for key, value in overrides.items():
        arguments.extend(['--config', f'{key}={toml_value(value)}'])
    return arguments


def validate_execution_config(cfg: Config, diagnostics: dict) -> dict:
    """Native, model-free config parsing in a disposable, unauthenticated home.

    This tests shared option syntax, not model availability or OS enforcement.
    No session/turn is created; the user's config/auth files are not copied.
    """
    executable = diagnostics['executable']
    env = execution_environment(diagnostics)
    try:
        help_result = subprocess.run([executable, 'app-server', '--help'], stdin=subprocess.DEVNULL,
                                     capture_output=True, text=True, encoding='utf-8', errors='replace',
                                     timeout=15, env=env,
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if help_result.returncode or '--print-startup-config' not in help_result.stdout:
            return {'exit_code': help_result.returncode or 2, 'error': 'CLI lacks --print-startup-config; update Codex for model-free configuration validation'}
        overrides = configuration_overrides(cfg)
        if cfg.isolation == 'workspace-write':
            overrides['sandbox_mode'] = 'workspace-write'
        with tempfile.TemporaryDirectory(prefix='codex-bench-config-') as directory:
            private = Path(directory)
            home, work = private / 'home', private / 'work'
            home.mkdir()
            work.mkdir()
            env['CODEX_HOME'] = str(home)
            command = [executable, '-C', str(work), 'app-server', '--print-startup-config', *override_arguments(overrides)]
            process = subprocess.run(command, cwd=work, stdin=subprocess.DEVNULL, capture_output=True,
                                     text=True, encoding='utf-8', errors='replace', timeout=20, env=env,
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        # Do not retain the effective startup config or personal account data.
        try:
            payload = json.loads(process.stdout)
        except ValueError:
            payload = None
        problem = payload.get('error') if isinstance(payload, dict) else None
        error = problem.get('message', str(problem)) if isinstance(problem, dict) else str(problem) if problem else None
        if not isinstance(payload, dict) or process.returncode and not error:
            error = error or process.stderr.strip()[:2000] or 'CLI did not return a valid startup configuration result'
        return {'exit_code': process.returncode, 'error': error, 'method': 'app-server --print-startup-config',
                'scope': 'shared CLI settings; no models or sessions; isolated unauthenticated config home'}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'exit_code': None, 'error': str(exc)}


def validate_execution_sandbox(cfg: Config, diagnostics: dict) -> dict:
    """Exercise Windows filesystem isolation without a model, login, or session.

    Syntax validation alone cannot detect the unelevated implementation's lack
    of denied-read support. Only disposable canaries are read by this command.
    """
    try:
        with tempfile.TemporaryDirectory(prefix='codex-bench-sandbox-') as directory:
            private = Path(directory)
            home, work, denied = (private / name for name in ('home', 'work', 'denied'))
            for path in (home, work, denied):
                path.mkdir()
            (denied / 'canary.txt').write_text('sandbox probe; no user data', encoding='utf-8')
            overrides = configuration_overrides(cfg)
            overrides['permissions']['benchmark']['filesystem'][str(denied)] = 'deny'
            # PowerShell is an OS runtime permitted by :minimal. It needs no
            # Python/Node packages or access to the runner repository.
            def literal(path):
                return "'" + str(path).replace("'", "''") + "'"
            script = (
                "$ErrorActionPreference='Stop'; "
                "[IO.File]::WriteAllText('probe.txt','ok'); "
                "$blocked=$false; try { [void][IO.File]::ReadAllText(" + literal(denied / 'canary.txt') +
                ") } catch { $blocked=$true }; if (-not $blocked) { exit 31 }; "
                "$blocked=$false; try { [IO.File]::WriteAllText(" + literal(denied / 'outside.txt') +
                ",'x') } catch { $blocked=$true }; if (-not $blocked) { exit 32 }; "
                "Write-Output 'BENCHMARK_SANDBOX_OK'"
            )
            shell = shutil.which('pwsh') or shutil.which('powershell') or 'powershell.exe'
            command = [diagnostics['executable'], *override_arguments(overrides), 'sandbox',
                       '--include-managed-config', '--permission-profile', 'benchmark', '--cd', str(work),
                       '--', shell, '-NoProfile', '-NonInteractive', '-Command', script]
            env = execution_environment(diagnostics)
            env.update(CODEX_HOME=str(home), TEMP=str(work), TMP=str(work), TMPDIR=str(work))
            process = subprocess.run(command, cwd=work, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                                     text=True, encoding='utf-8', errors='replace', timeout=35,
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            verified = (process.returncode == 0 and 'BENCHMARK_SANDBOX_OK' in process.stdout and
                        (work / 'probe.txt').is_file() and not (denied / 'outside.txt').exists())
            error = None if verified else (process.stderr.strip()[:2000] or
                    f'Filesystem canary check failed (exit {process.returncode}); restricted reads/writes are required')
        return {'exit_code': process.returncode, 'error': error,
                'method': 'codex sandbox; workspace write and denied external read/write canaries',
                'scope': 'Windows filesystem enforcement; no models, sessions, auth copies, or network probes'}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'exit_code': None, 'error': str(exc)}


def build_command(cfg: Config, job: Job, workspace: Path, diagnostics: dict) -> list[str]:
    command = [diagnostics.get("executable") or cfg.codex]
    if diagnostics.get("no_daemon"):
        command.append("--no-daemon")
    command.extend(["exec", "--json", "--model", job.model,
                    "--skip-git-repo-check", "--ephemeral", "--ignore-user-config"])
    if cfg.isolation == 'workspace-write':
        command.extend(['--sandbox', 'workspace-write'])
    command.extend(override_arguments({'model_reasoning_effort': job.effort, **configuration_overrides(cfg)}))
    command.extend(["--cd", str(workspace), "-"])
    return command


def classify_failure(exit_code: int | None, parsed: dict, stderr: str, termination: str | None) -> str | None:
    if termination:
        return termination
    # Warnings can contain "not supported" (PowerShell shell snapshots) even
    # after a successful generation. Only classify actual process/event failures.
    if exit_code == 0 and not parsed.get('errors'):
        return None if parsed.get('completed_event') else 'incomplete_events'
    message = (stderr + " " + json.dumps(parsed.get("errors", []))).lower()
    if any(x in message for x in ['error loading config', 'filesystempermissiontoml', 'failed to load configuration', 'startup configuration unavailable']):
        return 'configuration_error'
    if any(x in message for x in ["unauthorized", "authentication", "not logged", "401"]):
        return "authentication"
    if any(x in message for x in ["rate limit", "quota", "429", "usage limit"]):
        return "rate_limit"
    if any(x in message for x in ["sandbox", "permission profile", "permission denied", "access is denied"]):
        if parsed.get("errors") or exit_code != 0:
            return "sandbox_error"
    if any(x in message for x in ["not supported", "unsupported", "does not support", "unknown model", "invalid value", "not available for"]):
        return "unsupported_combination"
    if parsed.get("errors") or exit_code != 0:
        return "codex_error"
    if not parsed.get("completed_event"):
        return "incomplete_events"
    return None
