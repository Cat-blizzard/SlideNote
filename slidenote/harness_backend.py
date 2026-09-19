"""Subprocess adapter for the DeepSeek Harness 0.1.6-alpha.2 headless CLI.

The protocol is defined by the upstream release's
packages/bundle/headless/src/{startup,json-stream,index}.ts. A task is sent
verbatim on stdin; only the terminal final.text is returned to SlideNote.
Credentials and model selection remain owned by the Harness profile.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Sequence


HARNESS_VERSION = "0.1.6-alpha.2"


class HarnessError(RuntimeError):
    """A configuration, process, or headless-protocol failure."""


@dataclass(frozen=True)
class HarnessConfig:
    command: tuple[str, ...] = ("dsh",)
    profile: str = "headless"
    patches: tuple[Path, ...] = ()
    home: Path | None = None
    timeout_seconds: float = 600
    expected_version: str = HARNESS_VERSION

    def __post_init__(self) -> None:
        if isinstance(self.command, (str, bytes)) or not self.command:
            raise HarnessError("Harness command must be a non-empty argument sequence.")
        if any(not isinstance(arg, str) or not arg or "\0" in arg for arg in self.command):
            raise HarnessError("Harness command contains an invalid argument.")
        if not isinstance(self.profile, str) or not self.profile.strip():
            raise HarnessError("Harness profile must not be blank.")
        if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, (int, float)):
            raise HarnessError("Harness timeout must be a finite positive number.")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise HarnessError("Harness timeout must be a finite positive number.")
        if self.expected_version != HARNESS_VERSION:
            raise HarnessError(f"This adapter requires DeepSeek Harness {HARNESS_VERSION}.")


def _resolve_command(command: Sequence[str], *, windows: bool | None = None) -> list[str]:
    """Resolve npm/pnpm Windows Node shims without invoking a command shell.

    Standard dsh.cmd shims point to a quoted %dp0% or %~dp0% JavaScript
    entry point. Unknown batch wrappers are rejected with an actionable
    alternative instead of applying shell quoting to arbitrary arguments.
    """
    is_windows = os.name == "nt" if windows is None else windows
    executable = shutil.which(command[0])
    if executable is None:
        candidate = Path(command[0]).expanduser()
        if candidate.is_file():
            executable = str(candidate.resolve())
        else:
            raise HarnessError(
                "DeepSeek Harness executable was not found. Configure --harness-command "
                "with an installed dsh executable or node and its bin.js entry point."
            )
    resolved = [str(Path(executable).resolve()), *command[1:]]
    if not is_windows or Path(executable).suffix.lower() not in {".cmd", ".bat"}:
        return resolved
    shim = Path(executable).resolve()
    try:
        source = shim.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        raise HarnessError("Cannot read the Harness launcher; use node and bin.js explicitly.") from None
    match = re.search(
        r'"(?:%dp0%|%~dp0%?)[\\/]([^"\r\n]+\.(?:mjs|cjs|js))"\s+%\*',
        source,
        re.IGNORECASE,
    )
    if match is None:
        raise HarnessError(
            "Unsupported Harness batch launcher; configure --harness-command "
            "with node and the Harness bin.js entry point."
        )
    entrypoint = (shim.parent / match.group(1).replace("\\", "/")).resolve()
    if not entrypoint.is_file():
        raise HarnessError("The Harness batch launcher points to a missing JavaScript entry point.")
    local_node = shim.parent / "node.exe"
    node = str(local_node) if local_node.is_file() else shutil.which("node")
    if node is None:
        raise HarnessError("Node.js was not found; configure --harness-command with node and bin.js.")
    return [str(Path(node).resolve()), str(entrypoint), *command[1:]]


def parse_harness_events(stdout: str) -> tuple[str, dict[str, Any]]:
    """Validate the public --json stream and extract only its final answer.

    No event bodies are included in errors or metadata: intermediate model
    reasoning, tool output and provider diagnostics can contain credentials.
    """
    session_id: str | None = None
    final_text: str | None = None
    turn_reason: str | None = None
    count = 0
    tool_calls = 0
    usage_samples: list[dict[str, int | float]] = []
    usage_complete = True
    known_types = {"session", "status", "text", "thinking", "tool_call", "tool_result", "final", "error"}
    for line in stdout.split("\n"):
        if not line.strip():
            continue
        count += 1
        try:
            event = json.loads(line)
        except (ValueError, RecursionError):
            raise HarnessError(f"Harness emitted invalid JSON at event {count}.") from None
        if not isinstance(event, dict) or not isinstance(event.get("type"), str) or event["type"] not in known_types:
            raise HarnessError(f"Harness emitted an unsupported event at event {count}.")
        kind = event["type"]
        if final_text is not None:
            raise HarnessError("Harness emitted data after its terminal final event.")
        if kind == "error":
            raise HarnessError("Harness reported a runtime error; check its profile and credentials.")
        if kind == "session":
            if count != 1 or session_id is not None:
                raise HarnessError("Harness emitted an out-of-order session event.")
            session_id = event.get("sessionId")
            if not isinstance(session_id, str) or not session_id.strip():
                raise HarnessError("Harness emitted a session without a valid identity.")
        elif session_id is None:
            raise HarnessError("Harness emitted data before its opening session event.")
        elif kind == "final":
            if not isinstance(event.get("text"), str) or event.get("truncated") is True:
                raise HarnessError("Harness emitted an invalid final answer.")
            final_text = event["text"]
        elif kind == "status":
            phase = event.get("phase")
            if not isinstance(phase, str):
                raise HarnessError("Harness emitted an invalid status event.")
            if phase == "turn_end":
                reason = event.get("reason")
                if not isinstance(reason, dict) or not isinstance(reason.get("kind"), str):
                    raise HarnessError("Harness emitted a turn without a valid completion reason.")
                turn_reason = reason["kind"]
            elif phase == "step_end":
                usage = event.get("usage")
                if usage is None:
                    usage_complete = False
                elif (
                    not isinstance(usage, dict)
                    or not {"inputTokens", "outputTokens"}.issubset(usage)
                    or any(
                        isinstance(value, bool) or not isinstance(value, (int, float))
                        or not math.isfinite(value) or value < 0
                        for value in usage.values()
                    )
                ):
                    raise HarnessError("Harness emitted invalid token usage.")
                else:
                    usage_samples.append(usage)
        elif kind == "tool_call":
            tool_calls += 1
    if final_text is None:
        raise HarnessError("Harness exited without its terminal final event.")
    if turn_reason != "completed":
        raise HarnessError("Harness did not report a completed turn.")
    if not final_text.strip():
        raise HarnessError("Harness returned an empty final answer.")
    # Sum only complete usage samples and fields present in every step. Missing
    # data is unknown, never zero; upstream already combines retry attempts.
    usage_total = None
    if usage_samples and usage_complete:
        fields = set.intersection(*(set(sample) for sample in usage_samples))
        usage_total = {key: sum(sample[key] for sample in usage_samples) for key in sorted(fields)}
    return final_text, {
        "session_id": session_id,
        "event_count": count,
        "tool_calls": tool_calls,
        "usage": usage_total,
    }


class _WindowsJob:
    """Own the complete Windows process tree even after its root has exited.

    Child processes inherit job membership. KILL_ON_JOB_CLOSE kills orphaned
    tools as well; assigning a CREATE_SUSPENDED process before resuming its
    initial thread removes the process-start/assignment race.
    See https://learn.microsoft.com/windows/win32/procthread/job-objects.
    """

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_ulonglong)
                for name in (
                    "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
                )
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        class ThreadEntry(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ThreadID", wintypes.DWORD), ("th32OwnerProcessID", wintypes.DWORD),
                ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
            ]

        self._ctypes = ctypes
        self._thread_entry = ThreadEntry
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": (
                [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL,
            ),
            "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "CreateToolhelp32Snapshot": ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
            "Thread32First": ([wintypes.HANDLE, ctypes.POINTER(ThreadEntry)], wintypes.BOOL),
            "Thread32Next": ([wintypes.HANDLE, ctypes.POINTER(ThreadEntry)], wintypes.BOOL),
            "OpenThread": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "ResumeThread": ([wintypes.HANDLE], wintypes.DWORD),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self._kernel, name)
            function.argtypes = arguments
            function.restype = result
        self._handle = self._kernel.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self._kernel.SetInformationJobObject(
            self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits),
        ):  # JobObjectExtendedLimitInformation
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign_and_resume(self, pid: int) -> None:
        ctypes = self._ctypes
        kernel = self._kernel
        # PROCESS_SET_QUOTA | PROCESS_TERMINATE, required for job assignment.
        process_handle = kernel.OpenProcess(0x0100 | 0x0001, False, pid)
        if not process_handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not kernel.AssignProcessToJobObject(self._handle, process_handle):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel.CloseHandle(process_handle)
        # Popen closes the primary thread handle, so retrieve it through the
        # documented Toolhelp API. A suspended new process has not run its code.
        snapshot = kernel.CreateToolhelp32Snapshot(0x00000004, 0)  # TH32CS_SNAPTHREAD
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = self._thread_entry()
            entry.dwSize = ctypes.sizeof(entry)
            found = kernel.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.th32OwnerProcessID == pid:
                    thread = kernel.OpenThread(0x0002, False, entry.th32ThreadID)  # SUSPEND_RESUME
                    if not thread:
                        raise ctypes.WinError(ctypes.get_last_error())
                    try:
                        previous = kernel.ResumeThread(thread)
                        if previous == 0xFFFFFFFF:
                            raise ctypes.WinError(ctypes.get_last_error())
                        if previous > 0:
                            return
                    finally:
                        kernel.CloseHandle(thread)
                entry.dwSize = ctypes.sizeof(entry)
                found = kernel.Thread32Next(snapshot, ctypes.byref(entry))
            raise OSError("Cannot resume the owned Harness process.")
        finally:
            kernel.CloseHandle(snapshot)

    def close(self) -> None:
        if self._handle:
            self._kernel.CloseHandle(self._handle)
            self._handle = None


def _stop_process_tree(process: subprocess.Popen, job: _WindowsJob | None = None) -> None:
    """Terminate owned descendants and reap the root without touching pipe locks."""
    if job is not None:
        job.close()
    elif os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=2)
    except (OSError, subprocess.SubprocessError):
        pass
    # Never close a pipe here: Windows communicate() reader/writer threads can
    # own its buffered lock. Job termination releases all inherited writers,
    # and the I/O thread finishes and closes its own streams.


class HarnessRunner:
    """Thread-safe launcher; each call owns a fresh Harness process and Session."""

    def __init__(self, config: HarnessConfig, pack_dir: Path):
        self.config = config
        self.pack_dir = Path(pack_dir).resolve()
        if not self.pack_dir.is_dir():
            raise HarnessError("Harness working directory does not exist.")
        self._patches = tuple(Path(path).expanduser().resolve() for path in config.patches)
        if any(not path.is_file() for path in self._patches):
            raise HarnessError("A Harness patch file does not exist.")
        self._home = Path(config.home).expanduser().resolve() if config.home is not None else None
        self._command: list[str] | None = None
        self._version: str | None = None
        self._prepare_lock = threading.Lock()

    def _environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        if self._home is not None:
            environment["DSH_HOME"] = str(self._home)
        return environment

    def _execute(self, arguments: Sequence[str], *, prompt: str, timeout: float) -> str:
        kwargs: dict[str, Any] = {
            "cwd": str(self.pack_dir),
            "env": self._environment(),
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": False,
            "shell": False,
        }
        job = None
        process = None
        if os.name == "nt":
            # CREATE_SUSPENDED prevents tools spawning before job ownership.
            kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW | 0x00000004
            )
        else:
            kwargs["start_new_session"] = True
        try:
            if os.name == "nt":
                job = _WindowsJob()
            process = subprocess.Popen(list(arguments), **kwargs)
            if job is not None:
                job.assign_and_resume(process.pid)
        except BaseException as error:
            if process is not None:
                _stop_process_tree(process, job)
                # No I/O worker has started, so these pipes have no reader locks.
                for pipe in (process.stdin, process.stdout, process.stderr):
                    if pipe is not None:
                        pipe.close()
            elif job is not None:
                job.close()
            if isinstance(error, (OSError, ValueError)):
                raise HarnessError(
                    "Unable to start and supervise DeepSeek Harness; check its executable "
                    "and Node.js installation."
                ) from None
            raise

        result: list[tuple[bytes, bytes]] = []
        failures: list[BaseException] = []
        finished = threading.Event()

        def communicate() -> None:
            try:
                result.append(process.communicate(input=prompt.encode("utf-8"), timeout=timeout))
            except BaseException as error:
                failures.append(error)
            finally:
                finished.set()

        # Windows communicate() writes stdin synchronously before checking its
        # timeout. An outer deadline also bounds a child that never reads stdin.
        worker = threading.Thread(target=communicate, name="harness-io", daemon=True)
        try:
            worker.start()
            if not finished.wait(timeout):
                raise subprocess.TimeoutExpired(arguments, timeout)
            if failures:
                raise failures[0]
            stdout = result[0][0].decode("utf-8")
        except subprocess.TimeoutExpired:
            _stop_process_tree(process, job)
            raise HarnessError(f"DeepSeek Harness exceeded its {timeout:g}-second timeout.") from None
        except (OSError, UnicodeError):
            _stop_process_tree(process, job)
            raise HarnessError("Cannot read a valid UTF-8 response from DeepSeek Harness.") from None
        except BaseException:
            _stop_process_tree(process, job)
            raise
        finally:
            if job is not None:
                job.close()
            if worker.ident is not None:
                worker.join(timeout=2)
        if process.returncode != 0:
            raise HarnessError(
                f"DeepSeek Harness exited with status {process.returncode}; "
                "check its profile, credentials, permissions and model configuration."
            )
        return stdout

    def prepare(self) -> str:
        """Check the pinned runtime once, without starting a model request."""
        with self._prepare_lock:
            if self._version is not None:
                return self._version
            command = _resolve_command(self.config.command)
            output = self._execute(
                [*command, "--version"], prompt="", timeout=min(self.config.timeout_seconds, 30),
            )
            if output.strip() != self.config.expected_version:
                raise HarnessError(
                    f"This adapter requires DeepSeek Harness {self.config.expected_version}; "
                    "the configured executable reports a different version."
                )
            self._command = command
            self._version = self.config.expected_version
            return self._version

    def run(self, prompt: str) -> tuple[str, dict[str, Any]]:
        if not isinstance(prompt, str) or not prompt.strip():
            raise HarnessError("Harness prompt must not be blank.")
        version = self.prepare()
        command = [*self._command, "--profile", self.config.profile]
        for patch in self._patches:
            command.extend(["--patch", str(patch)])
        command.extend(["--json", "-"])
        started = time.monotonic()
        output = self._execute(command, prompt=prompt, timeout=self.config.timeout_seconds)
        final_text, metadata = parse_harness_events(output)
        metadata.update({
            "backend": "harness",
            "harness_version": version,
            "duration_seconds": round(time.monotonic() - started, 3),
        })
        return final_text, metadata
