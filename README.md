# oeqa-reporter

Run a Yocto oeqa testexport bundle against a board, and turn the result into a
self-contained HTML evidence report. Each test is paired with its slice of the
run log and a clip of the screen at that moment, so tests results can easily be
reviewed. Flashing, power, and serial are optional external commands, so the same
tool drives a full bench or just an already-booted target over SSH.

## Install

    uv tool install .

`ffmpeg` and `ffprobe` must be on PATH for video capture and clip cutting.

## Run

Produce a bundle on the build host (`bitbake <image> -c testexport`), copy it
over, and run it.

    oeqa-reporter ./<image> --target 192.168.1.50 --video /dev/video0

This waits for SSH, runs the bundle's `oe-test` over `--target-type simpleremote`,
collects dmesg, journal, and the serial console, and writes
`evidence-<timestamp>/index.html` with a per-test row, clip, and log. The board
is reached as root over key-based SSH.

Flashing and power cycling are optional and come from the environment;
see [docs/CONTROL.md](docs/CONTROL.md).

## Output

Each run writes an `evidence-<timestamp>/` directory; open `index.html` straight
from disk, everything it links is alongside it.

    index.html        the report: per-test status, clip, and log
    capture.mp4       full screen capture; clips/ holds the per-test cuts
    oe-test.log       the oeqa run log
    testresults.json  the oeqa results
    flash.log serial.log dmesg.log journal.log   collected during the run
