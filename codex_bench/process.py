"""Bounded subprocesses with process-tree cleanup and disk-backed raw logs."""
from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path


class WindowsJob:
    def __init__(self, process):
        from ctypes import wintypes as w

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", w.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", w.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = w.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


@dataclass
class ProcessResult:
    exit_code: int | None
    duration_seconds: float
    termination: str | None = None
    error: str | None = None


def run_process(command: list[str], *, cwd: Path, stdout: Path, stderr: Path,
                timeout: float, input_bytes: bytes = b"", cancel: threading.Event | None = None,
                max_output_bytes: int = 16 * 1024 * 1024, env: dict | None = None) -> ProcessResult:
    started = time.monotonic()
    process = job = None
    termination = error = None
    stdout.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryFile() as stdin, stdout.open("wb") as out, stderr.open("wb") as err:
            stdin.write(input_bytes)
            stdin.seek(0)
            process = subprocess.Popen(command, cwd=cwd, stdin=stdin, stdout=out, stderr=err,
                                       env=env, shell=False, start_new_session=os.name != "nt",
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if os.name == "nt":
                job = WindowsJob(process)
            while process.poll() is None:
                if cancel is not None and cancel.is_set():
                    termination = "interrupted"
                    break
                if time.monotonic() - started >= timeout:
                    termination = "timeout"
                    break
                if stdout.stat().st_size + stderr.stat().st_size > max_output_bytes:
                    termination = "output_limit"
                    break
                time.sleep(0.05)
            if stdout.stat().st_size + stderr.stat().st_size > max_output_bytes:
                termination = termination or "output_limit"
    except OSError as exc:
        error = str(exc)
        termination = "launch_error"
    finally:
        if job:
            job.close()
        elif process:
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif process.poll() is None:
                process.kill()
        if process:
            process.wait()
    return ProcessResult(process.returncode if process else None, time.monotonic() - started, termination, error)
