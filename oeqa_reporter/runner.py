"""Run an oeqa testexport bundle against a board and collect evidence."""
from __future__ import annotations

import glob
import shlex
import subprocess
import sys
import time
from pathlib import Path

from . import report

SSH_OPTS = ["-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR"]
SSH_WAIT_S = 600.0
POLL_S = 5.0
CASES_GLOB = "*/lib/oeqa/runtime/cases"


class RunError(Exception):
    pass


def resolve_bundle(bundle, artifact=None):
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
    raise RunError("no oe-test bundle under %s" % bundle)


def _one_artifact(pattern):
    matches = sorted(glob.glob(pattern))
    if len(matches) > 1:
        raise RunError("flash artifact pattern matched %d files: %s" % (len(matches), pattern))
    if matches:
        return matches[0]
    if Path(pattern).exists():
        return pattern
    raise RunError("flash artifact not found: %s" % pattern)


def _ssh(host, *args):
    return ["ssh", *SSH_OPTS, "root@" + host, *args]


def _tee(cmd, log, cwd=None):
    """Run cmd, stream combined output to stdout and a log file, return exit code."""
    with open(log, "wb") as logf:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for line in proc.stdout:
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
            logf.write(line)
        return proc.wait()


def _power(power_cmd, action):
    if not power_cmd:
        return
    rc = subprocess.call(shlex.split(power_cmd) + [action])
    if rc != 0:
        raise RunError("power %s failed (exit %d)" % (action, rc))


def _wait_for_ssh(host, timeout=SSH_WAIT_S):
    print("waiting for ssh...")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if subprocess.call(_ssh(host, "true"),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
            return
        time.sleep(POLL_S)
    raise RunError("no ssh on %s within %ds" % (host, timeout))


def _collect(host, command, dest):
    with open(dest, "wb") as f:
        subprocess.call(_ssh(host, command), stdout=f, stderr=subprocess.DEVNULL)


def _run_oetest(bundle, host, out, suites):
    cases = sorted(str(p.relative_to(bundle)) for p in bundle.glob(CASES_GLOB))
    if not cases:
        raise RunError("no oeqa runtime cases under %s/%s" % (bundle, CASES_GLOB))
    # --run-tests is greedy (nargs +), so it must follow the case directories.
    cmd = ["./oe-test", "-d", "runtime", "--target-type", "simpleremote",
           "--target-ip", host, "--json-result-dir", str(out.resolve()), *cases]
    if suites:
        cmd += ["--run-tests", *suites.split()]
    _tee(cmd, out / report.RUN_LOG, cwd=bundle)


def _terminate(proc):
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def run(bundle, target, *, video=None, suites=None, title=None, out=None,
        flash_cmd=None, flash_artifact=None, power_cmd=None, serial_cmd=None,
        skip_flash=False):
    if not target:
        raise RunError("a target address is required")
    host = target[5:] if target.startswith("root@") else target
    bundle = resolve_bundle(bundle, flash_artifact)
    out = Path(out) if out else Path("evidence-" + time.strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True, exist_ok=True)

    capture = serial = None
    try:
        if flash_cmd and flash_artifact and not skip_flash:
            rc = _tee(shlex.split(flash_cmd) + [_one_artifact(flash_artifact)], out / "flash.log")
            if rc != 0:
                raise RunError("flash failed (exit %d)" % rc)
        _power(power_cmd, "off")
        if video:
            capture = subprocess.Popen(
                ["ffmpeg", "-nostdin", "-y", "-f", "v4l2", "-i", video,
                 "-c:v", "libx264", "-preset", "ultrafast", str(out / report.CAPTURE)],
                stdout=open(out / "capture.log", "wb"), stderr=subprocess.STDOUT)
        _power(power_cmd, "on")
        if serial_cmd:
            # after the power ops: one transport may not drive power and console at once
            serial = subprocess.Popen(shlex.split(serial_cmd), stdin=subprocess.DEVNULL,
                                      stdout=open(out / "serial.log", "wb"), stderr=subprocess.STDOUT)
        _wait_for_ssh(host)
        _run_oetest(bundle, host, out, suites)
        _collect(host, "dmesg", out / "dmesg.log")
        _collect(host, "journalctl -b --no-pager", out / "journal.log")
    finally:
        _terminate(capture)
        _terminate(serial)
    return report.render(out, title or bundle.name)
