from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

from .config import Config, Job
from .util import BenchError, utc_now


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


def diagnose(executable: str) -> dict:
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
    return result


def build_command(cfg: Config, job: Job, workspace: Path, diagnostics: dict) -> list[str]:
    command = [diagnostics.get("executable") or cfg.codex]
    if diagnostics.get("no_daemon"):
        command.append("--no-daemon")
    command.extend(["exec", "--json", "--model", job.model,
                    "--skip-git-repo-check", "--ephemeral", "--ignore-user-config"])
    overrides = {"model_reasoning_effort": job.effort, "approval_policy": "never", "web_search": "disabled"}
    if cfg.isolation == "restricted":
        # --sandbox would override permission profiles. Inherit the workspace policy
        # instead, retaining its protected paths while denying reads outside the run.
        overrides.update({"default_permissions": "benchmark", "permissions.benchmark.extends": ":workspace",
                          'permissions.benchmark.filesystem.":root"': "deny",
                          'permissions.benchmark.filesystem.":minimal"': "read",
                          # Generation sets TMPDIR/TEMP/TMP to this run's own project.
                          'permissions.benchmark.filesystem.":tmpdir"': "write",
                          'permissions.benchmark.filesystem.":slash_tmp"': "deny",
                          'permissions.benchmark.filesystem.":workspace_roots"."."': "write",
                          "permissions.benchmark.network.enabled": False})
        # Exact denies also carve grading/results/source out of any broad runtime allow.
        protected = {cfg.output, cfg.grader_path, cfg.path.parent, Path(__file__).resolve().parent.parent}
        for path in sorted(protected, key=str):
            overrides[f"permissions.benchmark.filesystem.{json.dumps(str(path))}"] = "deny"
    else:
        command.extend(["--sandbox", "workspace-write"])
        overrides.update({"sandbox_workspace_write.network_access": False,
                          "sandbox_workspace_write.writable_roots": [],
                          "sandbox_workspace_write.exclude_tmpdir_env_var": True,
                          "sandbox_workspace_write.exclude_slash_tmp": True})
    for key, value in overrides.items():
        command.extend(["--config", f"{key}={json.dumps(value, ensure_ascii=False)}"])
    command.extend(["--cd", str(workspace), "-"])
    return command


def classify_failure(exit_code: int | None, parsed: dict, stderr: str, termination: str | None) -> str | None:
    if termination:
        return termination
    message = (stderr + " " + json.dumps(parsed.get("errors", []))).lower()
    if any(x in message for x in ["not supported", "unsupported", "does not support", "unknown model", "invalid value", "not available for"]):
        return "unsupported_combination"
    if any(x in message for x in ["unauthorized", "authentication", "not logged", "401"]):
        return "authentication"
    if any(x in message for x in ["rate limit", "quota", "429", "usage limit"]):
        return "rate_limit"
    if any(x in message for x in ["sandbox", "permission profile", "permission denied", "access is denied"]):
        if parsed.get("errors") or exit_code != 0:
            return "sandbox_error"
    if parsed.get("errors") or exit_code != 0:
        return "codex_error"
    if not parsed.get("completed_event"):
        return "incomplete_events"
    return None
