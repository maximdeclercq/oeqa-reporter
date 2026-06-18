# Board control

`oeqa-reporter run` flashes, power-cycles, and captures the serial console through
three external commands. Each is optional: leave it unset and that step is
skipped, so an already-booted board needs only `--target`. The commands and their
invocation match the standard Yocto testimage variables, so the same strings work
whether you run `bitbake -c testimage` (through a controller such as
meta-tegrademo's `TegraTarget`) or this tool.

| Step   | Flag               | Environment variable          | Invocation                          |
|--------|--------------------|-------------------------------|-------------------------------------|
| flash  | `--flash-cmd`      | `TEST_FLASHCONTROL_CMD`       | `CMD ARTIFACT`                      |
|        | `--flash-artifact` | `TEST_FLASHCONTROL_ARTIFACT`  | the `ARTIFACT` above (a glob is ok) |
| power  | `--power-cmd`      | `TEST_POWERCONTROL_CMD`       | `CMD [EXTRA] off`, `CMD [EXTRA] on` |
| serial | `--serial-cmd`     | `TEST_SERIALCONTROL_CMD`      | `CMD [EXTRA]`, stdout to serial.log |

`TEST_POWERCONTROL_EXTRA_ARGS` and `TEST_SERIALCONTROL_EXTRA_ARGS` are appended to
the power and serial commands, as testimage does. A flag overrides its variable.

The board is reached as root over key-based SSH: oe-test's `simpleremote` target
connects as root and has no password option, so the image under test must allow
passwordless root login over the network.

## Example: a Jetson over tegra-button

Export the same variables you would use for the testimage `TegraTarget` path, then
run the tool:

    export TEST_FLASHCONTROL_CMD='tegra-button flash'
    export TEST_FLASHCONTROL_ARTIFACT='demo-image-full-*.tegraflash-tar.zst'
    export TEST_POWERCONTROL_CMD='tegra-button power'    # -> tegra-button power on|off
    export TEST_SERIALCONTROL_CMD='tegra-button serial'  # stdout -> serial.log

    oeqa-reporter ./demo-image-full \
        --target 'fe80::4ab0:2dff:fef7:65b4%enp103s0f4u1u3' \
        --video /dev/video4

The serial console is started after the power commands on purpose: a controller
that bridges power and console over one transport (for example a single Pico CDC
pair) cannot do both at once.
