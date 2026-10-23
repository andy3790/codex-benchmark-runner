from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from .config import Config, Job
from .util import BenchError, utc_now


def diagnose(executable: str) -> dict:
    resolved = shutil.which(executable)
    result = {"checked_at": utc_now(), "executable": resolved, "version": None,
              "checks": {}, "errors": [], "warnings": []}
    if not resolved:
        result["errors"].append(f"Codex executable not found: {executable}")
        return result
    if Path(resolved).suffix.lower() in {".cmd", ".bat", ".ps1"}:
        result["errors"].append("Use the actual codex.exe/native binary, not a shell wrapper")
        return result
    for name, arguments in [("version", ["--version"]), ("root_help", ["--help"]), ("exec_help", ["exec", "--help"])]:
        try:
            proc = subprocess.run([resolved, *arguments], stdin=subprocess.DEVNULL, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=15,
                                  creationflags=subprocess.CREATE_NO_WINDOW if __import__("os").name == "nt" else 0)
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
