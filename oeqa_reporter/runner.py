"""Run an oeqa testexport bundle against a board and collect evidence."""
from __future__ import annotations

import glob
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import report

SSH_OPTS = ["-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR"]
SSH_WAIT_S = 600.0
POLL_S = 5.0
CASES_GLOB = "*/lib/oeqa/runtime/cases"
REMOTE_TARGET_LOG = "remoteTarget.log"     # oeqa writes per-command logs here, in oe-test's cwd

# oeqa logs every command at DEBUG to a 'target' logger pinned at INFO, so the
# per-command trail is dropped. Inject via PYTHONPATH to lift that logger to DEBUG.
CMDLOG_SHIM = '''\
import logging
_orig = logging.Logger.setLevel
def setLevel(self, level):
    _orig(self, logging.DEBUG if self.name == "target" else level)
logging.Logger.setLevel = setLevel
'''


class RunError(Exception):
    pass


def resolve_bundle(bundle: str | Path, artifact: str | None = None) -> Path:
    """Find the directory holding oe-test, accepting a parent of the bundle."""
    bundle = Path(bundle)
    if (bundle / "oe-test").is_file():
        return bundle
    subdirs = [d for d in sorted(bundle.iterdir()) if (d / "oe-test").is_file()]
    if artifact:
        name = Path(artifact).name
        for d in subdirs:
            if name.startswith(d.name):
                return d
    if len(subdirs) == 1:
        return subdirs[0]
    raise RunError(f"no oe-test bundle under {bundle}")


def _one_artifact(pattern: str) -> str:
    matches = sorted(glob.glob(pattern))
    if len(matches) > 1:
        raise RunError(f"flash artifact pattern matched {len(matches)} files: {pattern}")
    if matches:
        return matches[0]
    if Path(pattern).exists():
        return pattern
    raise RunError(f"flash artifact not found: {pattern}")


def _ssh(host: str, *args: str) -> list[str]:
    return ["ssh", *SSH_OPTS, f"root@{host}", *args]


def _tee(cmd: list[str], log: Path, cwd: Path | None = None,
         env: dict | None = None) -> int:
    """Run cmd, stream combined output to stdout and a log file, return exit code."""
    with open(log, "wb") as logf:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for line in proc.stdout:
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
            logf.write(line)
        return proc.wait()


def _power(power_cmd: str | None, action: str) -> None:
    if not power_cmd:
        return
    rc = subprocess.call(shlex.split(power_cmd) + [action])
    if rc != 0:
        raise RunError(f"power {action} failed (exit {rc})")


def _wait_for_ssh(host: str, timeout: float = SSH_WAIT_S) -> None:
    print("waiting for ssh...")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if subprocess.call(_ssh(host, "true"),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
            return
        time.sleep(POLL_S)
    raise RunError(f"no ssh on {host} within {timeout:.0f}s")


def _collect(host: str, command: str, dest: Path) -> None:
    with open(dest, "wb") as f:
        subprocess.call(_ssh(host, command), stdout=f, stderr=subprocess.DEVNULL)


def _run_oetest(bundle: Path, host: str, out: Path, suites: str | None) -> None:
    cases = sorted(str(p.relative_to(bundle)) for p in bundle.glob(CASES_GLOB))
    if not cases:
        raise RunError(f"no oeqa runtime cases under {bundle}/{CASES_GLOB}")
    # --run-tests is greedy (nargs +), so it must follow the case directories.
    # -d turns on oeqa's debug logging; the shim lifts the 'target' logger too.
    cmd = ["./oe-test", "-d", "runtime", "--target-type", "simpleremote",
           "--target-ip", host, "--json-result-dir", str(out.resolve()), *cases]
    if suites:
        cmd += ["--run-tests", *suites.split()]
    shim = out / ".cmdlog"
    shim.mkdir(exist_ok=True)
    (shim / "sitecustomize.py").write_text(CMDLOG_SHIM)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(shim.resolve()), env.get("PYTHONPATH", "")])
    (bundle / REMOTE_TARGET_LOG).unlink(missing_ok=True)  # drop any stale per-command log
    _tee(cmd, out / report.RUN_LOG, cwd=bundle, env=env)
    cmdlog = bundle / REMOTE_TARGET_LOG
    if cmdlog.is_file():
        shutil.copyfile(cmdlog, out / report.COMMANDS)


def _terminate(proc: subprocess.Popen | None) -> None:
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _stamp_stream(stream, path: Path) -> None:
    """Write the console stream to path, host wall-clock prefix per line so the
    report can bucket the UART into test windows."""
    with open(path, "w", errors="replace") as f:
        buf = bytearray()
        for chunk in iter(lambda: stream.read(256), b""):
            buf += chunk
            while b"\n" in buf:
                i = buf.index(b"\n")
                line = bytes(buf[:i]).decode("utf-8", "replace").replace("\x00", "").rstrip("\r")
                f.write("%s%03d %s\n" % (time.strftime("%H:%M:%S."), int(time.time() * 1000) % 1000, line))
                f.flush()
                del buf[:i + 1]


def run(bundle: str | Path, target: str, *, video: str | None = None, suites: str | None = None,
        title: str | None = None, out: str | Path | None = None, flash_cmd: str | None = None,
        flash_artifact: str | None = None, power_cmd: str | None = None,
        serial_cmd: str | None = None, skip_flash: bool = False) -> Path:
    if not target:
        raise RunError("a target address is required")
    host = target[5:] if target.startswith("root@") else target
    bundle = resolve_bundle(bundle, flash_artifact)
    out = Path(out) if out else Path(f"evidence-{time.strftime('%Y%m%d-%H%M%S')}")
    out.mkdir(parents=True, exist_ok=True)

    capture = serial = serial_writer = None
    try:
        if flash_cmd and flash_artifact and not skip_flash:
            rc = _tee(shlex.split(flash_cmd) + [_one_artifact(flash_artifact)], out / "flash.log")
            if rc != 0:
                raise RunError(f"flash failed (exit {rc})")
        _power(power_cmd, "off")
        if video:
            capture = subprocess.Popen(
                ["ffmpeg", "-nostdin", "-y", "-f", "v4l2", "-i", video,
                 "-c:v", "libx264", "-preset", "ultrafast", str(out / report.CAPTURE)],
                stdout=open(out / "capture.log", "wb"), stderr=subprocess.STDOUT)
            # wall-clock anchor: maps oe-test.log timestamps onto the video timeline
            (out / report.CAPTURE_START).write_text(repr(time.time()))
        _power(power_cmd, "on")
        if serial_cmd:
            # after the power ops: one transport may not drive power and console at once.
            # stdin is a held-open pipe, never DEVNULL: the bridge exits on stdin EOF.
            serial = subprocess.Popen(shlex.split(serial_cmd), stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            serial_writer = threading.Thread(target=_stamp_stream,
                                             args=(serial.stdout, out / "serial.log"), daemon=True)
            serial_writer.start()
        _wait_for_ssh(host)
        _run_oetest(bundle, host, out, suites)
        _collect(host, "dmesg", out / "dmesg.log")
        _collect(host, "journalctl -b --no-pager", out / "journal.log")
    finally:
        _terminate(capture)
        _terminate(serial)
        if serial_writer:
            serial_writer.join(timeout=3)  # let the stamper flush the tail of serial.log
    return report.render(out, title or bundle.name)
