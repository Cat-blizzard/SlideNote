import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import slidenote.harness_backend as harness
from slidenote.harness_backend import (
    HARNESS_VERSION,
    HarnessConfig,
    HarnessError,
    HarnessRunner,
    parse_harness_events,
)


def stream(*events):
    return "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n"


def success(text='{"title":"章节"}', *middle):
    return stream(
        {"type": "session", "sessionId": "session-test", "cwd": "."},
        *middle,
        {"type": "status", "phase": "turn_end", "reason": {"kind": "completed"}},
        {"type": "final", "text": text},
    )


def test_extracts_only_lossless_final_and_safe_metadata():
    final = '{"answer":"中文\n第二行\u2028原样"}'
    text, metadata = parse_harness_events(success(
        final,
        {"type": "thinking", "text": "PRIVATE REASONING"},
        {"type": "text", "text": "INTERMEDIATE ANSWER"},
        {"type": "tool_call", "tool": "read_file", "input": {"key": "SECRET"}},
        {"type": "tool_result", "status": "completed", "result": "TOOL OUTPUT"},
    ))
    assert text == final
    assert metadata["session_id"] == "session-test"
    assert metadata["tool_calls"] == 1
    assert metadata["usage"] is None
    for private in ("PRIVATE", "INTERMEDIATE", "SECRET", "TOOL OUTPUT"):
        assert private not in repr(metadata)


def test_usage_sums_only_fully_reported_fields():
    _, metadata = parse_harness_events(success(
        "answer",
        {"type": "status", "phase": "step_end",
         "usage": {"inputTokens": 5, "outputTokens": 2, "cacheReadTokens": 1}},
        {"type": "status", "phase": "step_end",
         "usage": {"inputTokens": 3, "outputTokens": 4}},
    ))
    assert metadata["usage"] == {"inputTokens": 8, "outputTokens": 6}
    _, incomplete = parse_harness_events(success(
        "answer",
        {"type": "status", "phase": "step_end", "usage": {"inputTokens": 5, "outputTokens": 2}},
        {"type": "status", "phase": "step_end"},
    ))
    assert incomplete["usage"] is None


@pytest.mark.parametrize("output", [
    "",
    "PRIVATE SECRET not json\n",
    '{"type":[]}\n',
    '[]\n',
    '{"type":"result","output":"wrong internal protocol"}\n',
    stream({"type": "error", "message": "PRIVATE SECRET"}),
    stream({"type": "final", "text": "no session"}),
    stream({"type": "session", "sessionId": "s"}),
    stream({"type": "session", "sessionId": 123}),
    success("") ,
    success("ok") + '{"type":"text","text":"late"}\n',
    success("ok") + '{"type":"final","text":"duplicate"}\n',
    success("ok", {"type": "session", "sessionId": "other"}),
    success("ok", {"type": "status", "phase": "step_end", "usage": {"inputTokens": -1}}),
    success("ok", {"type": "status", "phase": "turn_end", "reason": "invalid"}),
    success("ok").replace('"kind": "completed"', '"kind": "aborted"'),
    success("ok").replace('"kind": "completed"', '"kind": "error"'),
    success("ok").replace('"type": "final"', '"type": "final", "truncated": true'),
])
def test_rejects_failed_or_corrupt_stream_without_echoing_it(output):
    with pytest.raises(HarnessError) as caught:
        parse_harness_events(output)
    assert "SECRET" not in str(caught.value)
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True, "60"])
def test_rejects_invalid_timeout(timeout):
    with pytest.raises(HarnessError, match="timeout"):
        HarnessConfig(timeout_seconds=timeout)


@pytest.mark.parametrize("command", [(), "dsh --json", ("",), ("dsh", "\0")])
def test_rejects_non_argv_commands(command):
    with pytest.raises(HarnessError, match="command"):
        HarnessConfig(command=command)


def test_requires_the_verified_release():
    with pytest.raises(HarnessError, match=HARNESS_VERSION):
        HarnessConfig(expected_version="0.1.5-rc.2")


class FakeProcess:
    pid = 123456789

    def __init__(self, stdout, returncode=0, stderr=""):
        self.stdout_text = stdout
        self.stderr_text = stderr
        self.returncode = returncode
        self.communications = []

    def communicate(self, input=None, timeout=None):
        self.communications.append((input, timeout))
        return self.stdout_text.encode("utf-8"), self.stderr_text.encode("utf-8")


def install_fake_processes(monkeypatch, responses):
    launches = []
    processes = []

    def launch(argv, **kwargs):
        launches.append((argv, kwargs))
        response = responses[len(processes)]
        process = response if isinstance(response, FakeProcess) else FakeProcess(response)
        processes.append(process)
        return process

    class FakeJob:
        def assign_and_resume(self, pid):
            pass
        def close(self):
            pass
    monkeypatch.setattr(harness, "_WindowsJob", FakeJob)
    monkeypatch.setattr(harness, "_resolve_command", lambda command: ["resolved-dsh", *command[1:]])
    monkeypatch.setattr(harness.subprocess, "Popen", launch)
    return launches, processes


def test_launches_headless_with_stdin_no_shell_and_preserves_environment(tmp_path, monkeypatch):
    patch = tmp_path / "model patch.yaml"
    patch.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "SECRET")
    monkeypatch.delenv("DSH_PERMISSION_MODE", raising=False)
    config = HarnessConfig(
        patches=(patch,), home=tmp_path / "isolated-home", timeout_seconds=17,
    )
    launches, processes = install_fake_processes(
        monkeypatch, [HARNESS_VERSION + "\n", success("one"), success("two")],
    )
    runner = HarnessRunner(config, tmp_path)
    prompt = "中文 & echo PWNED\n$() %PATH% trailing newline\n"
    text, metadata = runner.run(prompt)
    assert text == "one"
    assert runner.run("next")[0] == "two"
    assert len(launches) == 3  # Cached version, two fresh invocations.
    assert launches[0][0] == ["resolved-dsh", "--version"]
    assert launches[1][0] == [
        "resolved-dsh", "--profile", "headless", "--patch", str(patch.resolve()), "--json", "-",
    ]
    assert prompt not in repr(launches[1][0])
    assert processes[1].communications == [(prompt.encode("utf-8"), 17)]
    options = launches[1][1]
    assert options["cwd"] == str(tmp_path.resolve())
    assert options["text"] is False
    assert options["shell"] is False
    assert options["env"]["DSH_HOME"] == str((tmp_path / "isolated-home").resolve())
    assert options["env"]["DEEPSEEK_API_KEY"] == "SECRET"
    assert "DSH_PERMISSION_MODE" not in options["env"]
    assert metadata["harness_version"] == HARNESS_VERSION
    assert "SECRET" not in repr(metadata)


def test_prepare_is_shared_across_concurrent_runs(tmp_path, monkeypatch):
    launches, _ = install_fake_processes(
        monkeypatch, [HARNESS_VERSION + "\n", *[success() for _ in range(4)]],
    )
    runner = HarnessRunner(HarnessConfig(), tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(runner.run, ["a", "b", "c", "d"]))
    assert len(results) == 4
    assert sum(argv[-1] == "--version" for argv, _ in launches) == 1


def test_version_mismatch_stops_before_model_invocation(tmp_path, monkeypatch):
    launches, _ = install_fake_processes(monkeypatch, ["0.1.5-rc.2\nSECRET"])
    with pytest.raises(HarnessError, match=HARNESS_VERSION) as caught:
        HarnessRunner(HarnessConfig(), tmp_path).run("do work")
    assert len(launches) == 1
    assert "SECRET" not in str(caught.value)


def test_nonzero_exit_rejects_even_valid_final_and_redacts_diagnostics(tmp_path, monkeypatch):
    install_fake_processes(monkeypatch, [
        HARNESS_VERSION,
        FakeProcess(success("partial"), returncode=1, stderr="DEEPSEEK_API_KEY=SECRET"),
    ])
    with pytest.raises(HarnessError, match="status 1") as caught:
        HarnessRunner(HarnessConfig(), tmp_path).run("prompt")
    assert "SECRET" not in str(caught.value)


def test_timeout_stops_owned_process_tree(tmp_path, monkeypatch):
    class SlowProcess(FakeProcess):
        def communicate(self, input=None, timeout=None):
            raise subprocess.TimeoutExpired(["hidden"], timeout, output="SECRET")

    slow = SlowProcess("")
    install_fake_processes(monkeypatch, [HARNESS_VERSION, slow])
    stopped = []
    monkeypatch.setattr(harness, "_stop_process_tree", lambda process, job: stopped.append(process))
    with pytest.raises(HarnessError, match="2-second timeout") as caught:
        HarnessRunner(HarnessConfig(timeout_seconds=2), tmp_path).run("prompt")
    assert stopped == [slow]
    assert "SECRET" not in str(caught.value)


def test_launch_failure_is_a_redacted_harness_error(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "_resolve_command", lambda command: ["dsh"])
    def fail(*args, **kwargs):
        raise OSError("SECRET")
    monkeypatch.setattr(harness.subprocess, "Popen", fail)
    with pytest.raises(HarnessError, match="Unable to start") as caught:
        HarnessRunner(HarnessConfig(), tmp_path).prepare()
    assert "SECRET" not in str(caught.value)


@pytest.mark.parametrize("prefix", ["%dp0%", "%~dp0", "%~dp0%"])
def test_resolves_windows_npm_and_pnpm_node_shims_without_shell(tmp_path, monkeypatch, prefix):
    shim = tmp_path / "dsh.cmd"
    script = tmp_path / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
    script.parent.mkdir(parents=True)
    script.write_text("// fixture", encoding="utf-8")
    node = tmp_path / "node.exe"
    node.touch()
    shim.write_text(f'@"%_prog%" "{prefix}\\node_modules\\@deepseek-ai\\dsh\\lib\\bin.js" %*',
                    encoding="utf-8")
    monkeypatch.setattr(harness.shutil, "which", lambda command: str(shim) if command == "dsh" else None)
    assert harness._resolve_command(("dsh",), windows=True) == [str(node), str(script)]


def test_refuses_unknown_windows_batch_wrappers(tmp_path, monkeypatch):
    shim = tmp_path / "dsh.cmd"
    shim.write_text("@echo unknown-wrapper", encoding="utf-8")
    monkeypatch.setattr(harness.shutil, "which", lambda command: str(shim))
    with pytest.raises(HarnessError, match="node and the Harness bin.js"):
        harness._resolve_command(("dsh",), windows=True)


def test_real_subprocess_unicode_stdin_and_final(tmp_path):
    """Exercise actual pipe encoding/argv/cwd without any model or network."""
    script = tmp_path / "fake harness.py"
    script.write_text(
        "import json, os, sys\n"
        f"if sys.argv[1:] == ['--version']:\n print({HARNESS_VERSION!r}); sys.exit(0)\n"
        "task = sys.stdin.buffer.read().decode('utf-8')\n"
        "events = ["
        "{'type':'session','sessionId':'fixture','cwd':os.getcwd()},"
        "{'type':'status','phase':'turn_end','reason':{'kind':'completed'}},"
        "{'type':'final','text':task}]\n"
        "sys.stdout.buffer.write(('\\n'.join(json.dumps(e, ensure_ascii=False) for e in events)"
        " + '\\n').encode('utf-8'))\n",
        encoding="utf-8",
    )
    task = "中文\r\n& %PATH% $(secret)\n"
    runner = HarnessRunner(HarnessConfig(command=(sys.executable, str(script))), tmp_path)
    text, metadata = runner.run(task)
    assert text == task
    assert metadata["session_id"] == "fixture"


def test_invalid_utf8_is_a_redacted_harness_error(tmp_path, monkeypatch):
    class InvalidUtf8(FakeProcess):
        def communicate(self, input=None, timeout=None):
            return b"\xffSECRET", b""
    invalid = InvalidUtf8("")
    install_fake_processes(monkeypatch, [HARNESS_VERSION, invalid])
    stopped = []
    monkeypatch.setattr(harness, "_stop_process_tree", lambda process, job: stopped.append(process))
    with pytest.raises(HarnessError, match="UTF-8") as caught:
        HarnessRunner(HarnessConfig(), tmp_path).run("prompt")
    assert stopped == [invalid]
    assert "SECRET" not in str(caught.value)


def test_real_subprocess_timeout_reaps_the_child(tmp_path):
    script = tmp_path / "slow harness.py"
    script.write_text(
        "import sys, time\n"
        f"if sys.argv[1:] == ['--version']:\n print({HARNESS_VERSION!r}); sys.exit(0)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    runner = HarnessRunner(
        HarnessConfig(command=(sys.executable, str(script)), timeout_seconds=0.5), tmp_path,
    )
    with pytest.raises(HarnessError, match="0.5-second timeout"):
        runner.run("prompt")


def test_orphan_inherited_pipe_is_killed_within_deadline(tmp_path):
    """An exited root must not leave its sleeping tool holding captured pipes."""
    parent = (
        "import subprocess, sys; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(15)'])"
    )
    runner = HarnessRunner(HarnessConfig(), tmp_path)
    started = harness.time.monotonic()
    with pytest.raises(HarnessError, match="0.3-second timeout"):
        runner._execute([sys.executable, "-c", parent], prompt="", timeout=0.3)
    elapsed = harness.time.monotonic() - started
    assert elapsed < 4, f"orphan pipe defeated the timeout: {elapsed:.2f}s"


def test_unread_large_stdin_is_bounded_by_outer_deadline(tmp_path):
    """Windows communicate's initial blocking stdin write also needs a deadline."""
    runner = HarnessRunner(HarnessConfig(), tmp_path)
    started = harness.time.monotonic()
    with pytest.raises(HarnessError, match="0.3-second timeout"):
        runner._execute(
            [sys.executable, "-c", "import time; time.sleep(15)"],
            prompt="中" * 1_000_000,
            timeout=0.3,
        )
    elapsed = harness.time.monotonic() - started
    assert elapsed < 4, f"blocked stdin defeated the timeout: {elapsed:.2f}s"


@pytest.mark.skipif(harness.os.name != "nt", reason="Windows Job Object lifecycle")
def test_windows_job_assignment_failure_kills_suspended_process(tmp_path, monkeypatch):
    real_popen = harness.subprocess.Popen
    children = []

    def track(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        children.append(process)
        return process

    def fail_assignment(self, pid):
        raise OSError("job assignment denied")

    monkeypatch.setattr(harness.subprocess, "Popen", track)
    monkeypatch.setattr(harness._WindowsJob, "assign_and_resume", fail_assignment)
    with pytest.raises(HarnessError, match="Unable to start and supervise"):
        HarnessRunner(HarnessConfig(), tmp_path)._execute(
            [sys.executable, "-c", "import time; time.sleep(15)"], prompt="", timeout=1,
        )
    assert len(children) == 1
    assert children[0].poll() is not None
    assert all(pipe.closed for pipe in (children[0].stdin, children[0].stdout, children[0].stderr))


@pytest.mark.skipif(harness.os.name != "nt", reason="Windows orphan process ownership")
def test_windows_orphan_tool_is_terminated_not_only_its_pipe(tmp_path):
    import ctypes
    from ctypes import wintypes

    pid_file = tmp_path / "tool.pid"
    parent = (
        "import pathlib, subprocess, sys; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(15)']); "
        f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid))"
    )
    with pytest.raises(HarnessError, match="timeout"):
        HarnessRunner(HarnessConfig(), tmp_path)._execute(
            [sys.executable, "-c", parent], prompt="", timeout=0.5,
        )
    assert pid_file.is_file(), "fixture must spawn a tool before the timeout"
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    child = kernel.OpenProcess(0x00100000, False, int(pid_file.read_text()))  # SYNCHRONIZE
    if child:
        try:
            assert kernel.WaitForSingleObject(child, 0) == 0, "orphan tool is still running"
        finally:
            kernel.CloseHandle(child)
    else:
        # A terminated process with no remaining handles disappears entirely.
        assert ctypes.get_last_error() == 87  # ERROR_INVALID_PARAMETER


@pytest.mark.skipif(harness.os.name != "nt", reason="Windows suspended process interruption")
def test_windows_startup_interruption_cleans_job_and_suspended_process(tmp_path, monkeypatch):
    real_popen = harness.subprocess.Popen
    children = []
    jobs = []

    def track(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        children.append(process)
        return process

    def interrupt_assignment(self, pid):
        jobs.append(self)
        raise KeyboardInterrupt

    monkeypatch.setattr(harness.subprocess, "Popen", track)
    monkeypatch.setattr(harness._WindowsJob, "assign_and_resume", interrupt_assignment)
    with pytest.raises(KeyboardInterrupt):
        HarnessRunner(HarnessConfig(), tmp_path)._execute(
            [sys.executable, "-c", "import time; time.sleep(15)"], prompt="", timeout=1,
        )
    assert len(children) == len(jobs) == 1
    assert children[0].poll() is not None
    assert jobs[0]._handle is None
    assert all(pipe.closed for pipe in (children[0].stdin, children[0].stdout, children[0].stderr))
