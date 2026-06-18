"""oeqa-reporter: run a Yocto oeqa bundle and render a video + log evidence report."""
from __future__ import annotations

import argparse
import os
import sys

from . import runner

__version__ = "0.1.0"
DESCRIPTION = "Run a Yocto oeqa testexport bundle and render a video + log evidence report."


def _with_extra(cmd_var, extra_var):
    cmd = os.environ.get(cmd_var)
    extra = os.environ.get(extra_var)
    return "%s %s" % (cmd, extra) if cmd and extra else cmd


def build_parser():
    ap = argparse.ArgumentParser(prog="oeqa-reporter", description=DESCRIPTION)
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("bundle", help="testexport bundle (has oe-test), or its parent")
    ap.add_argument("-t", "--target", default=os.environ.get("TEST_TARGET_IP"),
                    help="DUT address, reached as root over key-based SSH (default: $TEST_TARGET_IP)")
    ap.add_argument("--video", metavar="DEV", help="v4l2 capture device, e.g. /dev/video4")
    ap.add_argument("--suites", default=os.environ.get("TEST_SUITES"), metavar="MODULES",
                    help="oe-test --run-tests modules (default: $TEST_SUITES; else the bundle's)")
    ap.add_argument("--title", help="report title (default: bundle name)")
    ap.add_argument("--out", metavar="DIR", help="evidence directory (default: evidence-<timestamp>)")
    g = ap.add_argument_group("board control",
                              "each defaults to its testimage env var; unset -> step skipped")
    g.add_argument("--flash-cmd", default=os.environ.get("TEST_FLASHCONTROL_CMD"), metavar="CMD",
                   help="flash command, run as: CMD ARTIFACT (default: $TEST_FLASHCONTROL_CMD)")
    g.add_argument("--flash-artifact", default=os.environ.get("TEST_FLASHCONTROL_ARTIFACT"),
                   metavar="PATH", help="image artifact for the flash command, glob ok "
                   "(default: $TEST_FLASHCONTROL_ARTIFACT)")
    g.add_argument("--power-cmd", default=_with_extra("TEST_POWERCONTROL_CMD", "TEST_POWERCONTROL_EXTRA_ARGS"),
                   metavar="CMD", help="power command, run as: CMD on|off (default: $TEST_POWERCONTROL_CMD)")
    g.add_argument("--serial-cmd", default=_with_extra("TEST_SERIALCONTROL_CMD", "TEST_SERIALCONTROL_EXTRA_ARGS"),
                   metavar="CMD", help="serial console, stdout -> serial.log (default: $TEST_SERIALCONTROL_CMD)")
    g.add_argument("--skip-flash", action="store_true", help="reuse an already-booted board")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.target:
        print("error: a target is required (pass --target or set $TEST_TARGET_IP)", file=sys.stderr)
        return 2
    try:
        index = runner.run(
            args.bundle, args.target,
            video=args.video, suites=args.suites, title=args.title, out=args.out,
            flash_cmd=args.flash_cmd, flash_artifact=args.flash_artifact,
            power_cmd=args.power_cmd, serial_cmd=args.serial_cmd, skip_flash=args.skip_flash,
        )
    except (runner.RunError, OSError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    print("wrote %s" % index)
    return 0


if __name__ == "__main__":
    sys.exit(main())
