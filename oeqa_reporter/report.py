"""Render an oeqa evidence directory into a self-contained index.html."""
from __future__ import annotations

import html
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from ._fonts import FONT_CSS

# evidence directory layout; the runner writes these, the report links them
CAPTURE = "capture.mp4"
CAPTURE_START = "capture.start"              # wall-clock epoch of video t=0
RUN_LOG = "oe-test.log"
COMMANDS = "commands.log"                    # per-command trail
RESULTS = "testresults.json"
SUMMARY = "summary.txt"
INDEX = "index.html"
CLIPS = "clips"
COLLECTED_LOGS = ("flash.log", "serial.log", "commands.log", "dmesg.log", "journal.log")

BLEED = 4.0                                  # seconds kept around each test window
BODY_TAIL = 120                              # log lines kept per test before truncating the head
LOG_TAIL = 400                               # lines kept when inlining a collected log
CMD_TAIL = 200                               # command lines kept per test
SERIAL_TEST_CAP = 1200                       # console lines kept per test
STAMP = "%Y-%m-%d %H:%M:%S,%f"
LOGLINE = re.compile(r"^(\d{4}-\d\d-\d\d [\d:,]+) - [\w.]+ - \w+ - (.*)")
CMDLINE = re.compile(r"^(\d\d:\d\d:\d\d\.\d+) \w+: (.*)")  # remoteTarget.log: time-of-day only
SERIALLINE = re.compile(r"^(\d\d:\d\d:\d\d\.\d+) (.*)")  # host-stamped console line
SSH_NOISE = re.compile(r"\[Running\]\$ ssh -l root .*?export PATH=[^;]*; ")  # repeated ssh wrapper
# anchored: the inline start is "test_x (id)"; the end-of-run summary lines are
# "FAIL: test_x (id)" / "ERROR: test_x (id)" and must not open a second window
TESTSTART = re.compile(r"^(test_\w+) \(([\w.]+)\)$")
RESULT = re.compile(r"\.\.\. (ok|FAIL|ERROR|skipped)\b")  # skipped carries a trailing 'reason'
ORDER = {"FAILED": 0, "ERROR": 1, "SKIPPED": 2, "PASSED": 3}
STATUS_ORDER = ("PASSED", "FAILED", "ERROR", "SKIPPED")
COUNT_LABEL = {"PASSED": "passed", "FAILED": "failed", "ERROR": "errors", "SKIPPED": "skipped"}
ICON = {"PASSED": '<polyline points="6 12.5 10.5 17 18 7"/>',
        "FAILED": '<line x1="7" y1="7" x2="17" y2="17"/><line x1="17" y1="7" x2="7" y2="17"/>',
        "ERROR": '<line x1="12" y1="6.5" x2="12" y2="13"/><line x1="12" y1="16.8" x2="12" y2="17.2"/>',
        "SKIPPED": '<line x1="7" y1="12" x2="17" y2="12"/>'}


def sh(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


def video_seconds(path: Path) -> float:
    out = sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
              "-of", "default=nw=1:nk=1", str(path)])
    return float(out) if out else 0.0


def clip(video: Path, start: float, end: float, dest: Path) -> None:
    # re-encode, not -c copy: stream-copy snaps to keyframes and blanks short windows
    start = max(0.0, start)
    dur = max(0.5, end - start)
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-ss", f"{start:.2f}", "-i", str(video),
                    "-t", f"{dur:.2f}", "-an", "-c:v", "libx264", "-preset", "ultrafast",
                    "-movflags", "+faststart", str(dest)], capture_output=True)


def parse_log(path: Path) -> dict[str, dict]:
    """Map each test id to its {start, end, body} window in the timestamped run log.

    The window closes at the result line. oeqa dumps the failure traceback right
    after that line, but testresults.json already carries it, so it is left out
    here to avoid rendering the same traceback twice.
    """
    tests, cur = {}, None
    for line in path.read_text(errors="replace").splitlines():
        m = LOGLINE.match(line)
        if not m:
            if cur:
                tests[cur]["body"].append(line)
            continue
        when, text = datetime.strptime(m.group(1), STAMP).timestamp(), m.group(2)
        start = TESTSTART.search(text)
        if start:
            cur = start.group(2)
            tests[cur] = {"start": when, "end": when, "body": []}
        elif cur and RESULT.search(text):
            tests[cur]["end"] = when
            cur = None
        elif cur:
            tests[cur]["end"] = when
            tests[cur]["body"].append(text)
    return tests


def parse_commands(path: Path, day: str) -> list[tuple[float, str]]:
    """Timestamp each command line so it buckets into a test window; continuation
    lines fold into the prior entry. remoteTarget.log has only a time-of-day, so the
    run's date pins it onto the same wall-clock as parse_log."""
    entries: list[list] = []
    for line in path.read_text(errors="replace").splitlines():
        m = CMDLINE.match(line)
        if m:
            when = datetime.strptime("%s %s" % (day, m.group(1)), "%Y-%m-%d %H:%M:%S.%f").timestamp()
            entries.append([when, line])
        elif entries:
            entries[-1][1] += "\n" + line
    return [(w, t) for w, t in entries]


def parse_serial(path: Path, day: str) -> list[tuple[float, str]]:
    """Host-stamped console lines, pinned to the run's date so each buckets into a
    test window."""
    entries = []
    for line in path.read_text(errors="replace").splitlines():
        m = SERIALLINE.match(line)
        if m:
            when = datetime.strptime("%s %s" % (day, m.group(1)), "%Y-%m-%d %H:%M:%S.%f").timestamp()
            entries.append((when, m.group(2)))
    return entries


def status_icon(status: str) -> str:
    return ('<svg viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width="2.4" '
            f'stroke-linecap="round" stroke-linejoin="round">{ICON.get(status, ICON["SKIPPED"])}</svg>')


def _cap_console(console: str) -> str:
    """Head+tail a long console block so a reboot/boot window does not swamp the page."""
    lines = console.splitlines()
    if len(lines) <= SERIAL_TEST_CAP:
        return console
    half = SERIAL_TEST_CAP // 2
    return ("\n".join(lines[:half])
            + "\n... (%d console lines omitted; full console in serial.log)\n" % (len(lines) - SERIAL_TEST_CAP)
            + "\n".join(lines[-half:]))


def section(title: str, summary: dict, body: list[str]) -> str:
    status = summary["status"]
    open_attr = " open" if status == "FAILED" else ""  # errors are often cascades; keep them collapsed
    path, _, leaf = title.rpartition(".")
    name = (f'<span class=path>{html.escape(path)}.</span>' if path else "") + \
           f'<span class=leaf>{html.escape(leaf)}</span>'
    video = (f'<video controls preload=none src="{summary["clip"]}"></video>'
             if summary.get("clip") else "")
    cmds = SSH_NOISE.sub("$ ", summary.get("commands") or "")
    clines = cmds.splitlines()
    if len(clines) > CMD_TAIL:
        cmds = "... (%d earlier command lines omitted)\n" % (len(clines) - CMD_TAIL) + "\n".join(clines[-CMD_TAIL:])
    cmdblock = (f'<div class=log><span class=cap>commands</span><pre>{html.escape(cmds)}</pre></div>'
                if cmds else "")
    console = _cap_console(summary.get("console") or "")
    consoleblock = (f'<div class=log><span class=cap>console (uart)</span><pre>{html.escape(console)}</pre></div>'
                    if console else "")
    log = "\n".join(body).strip()
    cap = {"FAILED": "traceback", "ERROR": "traceback", "SKIPPED": "reason"}.get(status, "output")
    panel = (f'<div class=log><span class=cap>{cap}</span><pre>{html.escape(log)}</pre></div>'
             if log else '<p class=muted>no captured output</p>')
    return (f'<details{open_attr} data-status="{status}" data-name="{html.escape(title)}">'
            f'<summary><span class=badge>{status_icon(status)}</span>'
            f'<span class=name>{name}</span><span class=stag>{status}</span>'
            f'<span class=dur>{summary["dur"]:.1f}s</span>'
            f'<svg class=chev viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=2.2 '
            f'stroke-linecap=round stroke-linejoin=round><polyline points="9 6 15 12 9 18"/></svg>'
            f'</summary><div class=panel>{video}{panel}{cmdblock}{consoleblock}</div></details>')


def _logpre(body: str) -> str:
    return f'<div class=log><pre>{html.escape(body)}</pre></div>'


def _logfile(label: str, meta: str, panel: str) -> str:
    return (f'<details class=logfile data-status="LOG" data-name="{html.escape(label)}"><summary>'
            f'<span class=fileicon><svg viewBox="0 0 24 24" fill=none stroke=currentColor '
            f'stroke-width=2 stroke-linecap=round stroke-linejoin=round>'
            f'<path d="M7 4h7l3.5 3.5V20H7Z"/><polyline points="14 4 14 7.5 17.5 7.5"/></svg></span>'
            f'<span class=name><span class=leaf>{html.escape(label)}</span></span>'
            f'<span class=dur>{html.escape(meta)}</span>'
            f'<svg class=chev viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=2.2 '
            f'stroke-linecap=round stroke-linejoin=round><polyline points="9 6 15 12 9 18"/></svg>'
            f'</summary><div class=panel>{panel}</div></details>')


def log_section(name: str, text: str) -> str:
    """Inline a collected log as a collapsed section, tail-capped to bound page size."""
    lines = text.splitlines()
    body = "\n".join(lines)
    if len(lines) > LOG_TAIL:
        body = (f"... ({len(lines) - LOG_TAIL} earlier lines omitted; full {name} alongside this report)\n"
                + "\n".join(lines[-LOG_TAIL:]))
    return _logfile(name, f"{len(lines)} lines", _logpre(body))


def capture_section(name: str, dur: float) -> str:
    """The full screen capture as a file row whose body is an inline player."""
    glyph = ('<svg viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=2 '
             'stroke-linecap=round stroke-linejoin=round>'
             '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M10 9.2 15 12l-5 2.8z"/></svg>')
    return (f'<details class=logfile data-status="LOG" data-name="{html.escape(name)}"><summary>'
            f'<span class=fileicon>{glyph}</span>'
            f'<span class=name><span class=leaf>{html.escape(name)}</span></span>'
            f'<span class=dur>{dur:.0f}s</span>'
            f'<svg class=chev viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=2.2 '
            f'stroke-linecap=round stroke-linejoin=round><polyline points="9 6 15 12 9 18"/></svg>'
            f'</summary><div class=panel><video controls preload=none src="{name}"></video></div></details>')


def boot_section(clip_src: str | None, console: str) -> str:
    """The power-on boot as one collapsed row, kept out of the first test."""
    vid = f'<video controls preload=none src="{clip_src}"></video>' if clip_src else ""
    con = (f'<div class=log><span class=cap>console (uart)</span><pre>{html.escape(console)}</pre></div>'
           if console else "")
    return _logfile("boot / power-on", "power-on to first test", vid + con)


def render(evidence: str | Path, title: str | None = None) -> Path:
    """Write index.html into the evidence directory and return its path."""
    ev = Path(evidence)
    title = title or ev.name or "oeqa report"
    results = {}
    for entry in json.loads((ev / RESULTS).read_text()).values():
        results.update(entry.get("result", {}))  # merge every configuration in the file
    run_log = ev / RUN_LOG
    logged = parse_log(run_log) if run_log.exists() else {}
    video = ev / CAPTURE
    have_video = video.exists() and bool(logged)
    dur = video_seconds(video) if have_video else 0.0
    cap_start = ev / CAPTURE_START
    if have_video and cap_start.exists():
        anchor = float(cap_start.read_text().strip())   # absolute: wall-clock of video t=0
    elif have_video:
        anchor = max(t["end"] for t in logged.values()) - dur  # legacy self-calibration
    else:
        anchor = 0.0
    # the power-on boot gets its own row, not folded into the first test
    first_tid = min(logged, key=lambda t: logged[t]["start"]) if logged else None
    if have_video:
        (ev / CLIPS).mkdir(exist_ok=True)

    # bucket host-stamped command/console lines into each test window
    day = (datetime.fromtimestamp(min(t["start"] for t in logged.values())).strftime("%Y-%m-%d")
           if logged else "")
    cmd_log = ev / COMMANDS
    cmd_entries = parse_commands(cmd_log, day) if (cmd_log.exists() and day) else []
    serial_log = ev / "serial.log"
    serial_entries = parse_serial(serial_log, day) if (serial_log.exists() and day) else []

    counts, items = {}, []
    for tid, r in results.items():
        status = r.get("status")
        if not status:
            continue  # non-test blobs (ltp rawlogs/sections) sit in result with no status
        counts[status] = counts.get(status, 0) + 1
        body = list(logged.get(tid, {}).get("body", []))
        if len(body) > BODY_TAIL:
            body = [f"... ({len(body) - BODY_TAIL} earlier log lines omitted)"] + body[-BODY_TAIL:]
        if r.get("log"):
            body += ["", r["log"]]
        clip_src = None
        if have_video and tid in logged:
            clip_src = f"{CLIPS}/{tid.split('.')[0]}.{tid.split('.')[-1]}.mp4"
            start = logged[tid]["start"] - anchor - BLEED
            clip(video, start, logged[tid]["end"] - anchor + BLEED, ev / clip_src)
        w = logged.get(tid)
        cmds = "\n".join(t for ts, t in cmd_entries if w and w["start"] <= ts <= w["end"]) if cmd_entries else ""
        lo = w["start"] - BLEED if w else 0
        console = "\n".join(t for ts, t in serial_entries
                            if w and lo <= ts <= w["end"] + BLEED) if serial_entries else ""
        items.append((ORDER.get(status, 9), logged.get(tid, {}).get("start", 0),
                      section(tid, {"status": status, "dur": float(r.get("duration") or 0.0),
                                    "clip": clip_src, "commands": cmds, "console": console}, body)))

    total = sum(counts.values())
    passed = counts.get("PASSED", 0)
    executed = passed + counts.get("FAILED", 0) + counts.get("ERROR", 0)
    pct = round(100 * passed / executed) if executed else 0
    present = [s for s in STATUS_ORDER if counts.get(s)]
    chips = f'<button class="chip on no-dot" data-filter=ALL>{total} total</button>' + "".join(
        f'<button class=chip data-s={s} data-filter={s}>{counts[s]} {COUNT_LABEL[s]}</button>'
        for s in present)
    # the bar and percentage score executed tests; skips are reported, not scored
    bar = "".join(f'<span class=seg data-s={s} style="width:{counts[s] / executed * 100:.4f}%"></span>'
                  for s in ("PASSED", "FAILED", "ERROR") if counts.get(s)) if executed else ""
    (ev / SUMMARY).write_text(
        ev.name + "\n" + "  ".join(f"{v} {k}" for k, v in sorted(counts.items())) + "\n")

    files = capture_section(CAPTURE, dur) if have_video else ""
    if first_tid:
        first_start = logged[first_tid]["start"]
        boot_clip = None
        if have_video:
            boot_clip = f"{CLIPS}/boot.mp4"
            clip(video, 0.0, first_start - anchor + BLEED, ev / boot_clip)
        boot_console = (_cap_console("\n".join(t for ts, t in serial_entries if ts < first_start))
                        if serial_entries else "")
        if boot_clip or boot_console:
            files += boot_section(boot_clip, boot_console)
    # commands fold into the per-test trail; serial too when host-stamped, else whole
    embed = [n for n in (RUN_LOG, *COLLECTED_LOGS)
             if n != COMMANDS and not (n == "serial.log" and serial_entries)]
    files += "".join(log_section(name, (ev / name).read_text(errors="replace"))
                     for name in embed
                     if (ev / name).exists() and (ev / name).stat().st_size)
    if files:
        files = f'<section class=files><h2 class=group>files</h2>{files}</section>'

    rows = "\n".join(s for _, _, s in sorted(items))
    title = html.escape(title)
    index = ev / INDEX
    index.write_text(
        f'<!doctype html><html lang=en><head><meta charset=utf-8>'
        f'<meta name=viewport content="width=device-width,initial-scale=1">'
        f'<title>{title}</title><script>{HEAD_SCRIPT}</script>'
        f'<style>{FONT_CSS}{STYLE}</style></head><body>'
        f'<header class=report-head>'
        f'<div class=topbar><p class=kind>oeqa runtime report</p>{THEME_BTN}</div>'
        f'<h1>{title}</h1>'
        f'<div class=summary><div class=bar>{bar}</div>'
        f'<div class=rate><span class=pct>{pct}%</span> <span class=ratel>passed</span></div></div>'
        f'</header>{files}'
        f'<div class=controls><div class=chips>{chips}</div>'
        f'<input class=search type=search aria-label="filter tests by name" '
        f'placeholder="Filter tests by name"></div>'
        f'<main>{rows}</main><script>{SCRIPT}</script></body></html>')
    return index


THEME_BTN = ('<button class=themebtn type=button aria-label="toggle color theme" title="theme">'
             '<svg viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.7 '
             'stroke-linecap=round stroke-linejoin=round></svg></button>')

HEAD_SCRIPT = ("try{var t=localStorage.getItem('oeqa-theme');"
               "if(t==='light'||t==='dark')document.documentElement.setAttribute('data-theme',t)}catch(e){}")

# light = Yocto docs palette (accent #00557d). status colors are CSS vars so they
# flip with the theme; the markup only ever emits status names, never colors.
STYLE = """
:root{
 --bg:#fff;--card:#fff;--hover:#f2f6f8;--line:#e7eaed;--border:#d7dde2;--faint:#9aa3ab;
 --text:#222b33;--muted:#5b6770;--accent:#00557d;--soft:rgba(0,85,125,.10);
 --pass:#1a7f4b;--fail:#cf2e2e;--error:#b25e00;--skip:#76828c;--on:#fff;
 --sans:"Lato",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
 --head:"Roboto Slab","Lato",Georgia,serif;
 --mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,Consolas,monospace;
 --label:.72rem;--meta:.78rem;--name:.9rem;--code:.8rem;--pad:1.5rem;--wrap:60rem}
/* Yocto sites are light; this neutral-dark mirrors the cgit/autobuilder tooling look */
[data-theme=dark]{--bg:#1b1b1d;--card:#242427;--hover:#2c2c30;--line:#303035;--border:#3a3a40;--faint:#74747c;
 --text:#e2e2e4;--muted:#9b9ba2;--accent:#4d9ed8;--soft:rgba(77,158,216,.16);
 --pass:#54b568;--fail:#e85d5d;--error:#d39a2e;--skip:#8a8a92;--on:#16161a}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#1b1b1d;--card:#242427;--hover:#2c2c30;
 --line:#303035;--border:#3a3a40;--faint:#74747c;--text:#e2e2e4;--muted:#9b9ba2;--accent:#4d9ed8;
 --soft:rgba(77,158,216,.16);--pass:#54b568;--fail:#e85d5d;--error:#d39a2e;--skip:#8a8a92;--on:#16161a}}
[data-status=PASSED],[data-s=PASSED]{--s:var(--pass)}
[data-status=FAILED],[data-s=FAILED]{--s:var(--fail)}
[data-status=ERROR],[data-s=ERROR]{--s:var(--error)}
[data-status=SKIPPED],[data-s=SKIPPED]{--s:var(--skip)}
[data-status=LOG]{--s:var(--border)}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.55 var(--sans);
 -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
.report-head{max-width:var(--wrap);margin:0 auto;padding:2.2rem var(--pad) 1.1rem}
.topbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:1.1rem}
.kind{margin:0;font:400 var(--meta)/1 var(--sans);color:var(--muted)}
.themebtn{display:grid;place-items:center;width:2rem;height:2rem;color:var(--muted);cursor:pointer;
 background:var(--card);border:1px solid var(--border);border-radius:8px;transition:.15s}
.themebtn:hover{color:var(--text);border-color:var(--accent)}
.themebtn svg{width:16px;height:16px}
h1{margin:0 0 1.3rem;font:600 1.62rem/1.2 var(--head);color:var(--text);word-break:break-word}
.summary{display:flex;align-items:center;gap:.9rem}
.bar{flex:1;display:flex;height:6px;border-radius:999px;overflow:hidden;background:var(--line)}
.rate{flex:none;display:flex;align-items:baseline;gap:.32rem}
.pct{font:600 1.05rem/1 var(--mono);letter-spacing:-.01em;color:var(--text)}
.ratel{font:400 var(--meta)/1 var(--sans);color:var(--muted)}
.seg{display:block;height:100%;background:var(--s)}
.controls{position:sticky;top:0;z-index:5;max-width:var(--wrap);margin:0 auto;
 display:flex;flex-wrap:wrap;align-items:center;gap:.55rem;padding:.75rem var(--pad);
 background:var(--card);background:color-mix(in srgb,var(--bg) 80%,transparent);
 backdrop-filter:blur(14px) saturate(150%);-webkit-backdrop-filter:blur(14px) saturate(150%);
 border-bottom:1px solid var(--line)}
.chips{display:flex;flex-wrap:wrap;gap:.4rem}
.chip{display:inline-flex;align-items:center;gap:.5rem;cursor:pointer;height:2rem;padding:0 .8rem;
 font:400 var(--meta)/1 var(--mono);color:var(--muted);
 background:var(--card);border:1px solid var(--border);border-radius:8px;transition:.15s}
.chip::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--s,transparent)}
.chip.no-dot::before{display:none}
.chip:hover{color:var(--text);border-color:var(--faint)}
.chip:focus{outline:none}
.chip:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.chip.on{color:var(--accent);border-color:var(--accent);background:var(--soft)}
.search{flex:1;min-width:12rem;height:2rem;padding:0 .8rem;font:400 var(--meta)/1 var(--sans);color:var(--text);
 background:var(--card);border:1px solid var(--border);border-radius:8px;outline:none;transition:.15s}
.search::placeholder{color:var(--faint)}
.search:focus{border-color:var(--accent)}
main{max-width:var(--wrap);margin:1.35rem auto 5rem;padding:0 var(--pad);display:flex;flex-direction:column;gap:.5rem}
details{background:var(--card);border:1px solid var(--border);border-radius:10px;overflow:hidden;transition:border-color .15s}
details:hover{border-color:var(--faint)}
details[hidden]{display:none}
summary{display:grid;grid-template-columns:auto 1fr auto auto auto;align-items:center;gap:.8rem;
 padding:.8rem .9rem;cursor:pointer;list-style:none}
summary::-webkit-details-marker{display:none}
summary:hover{background:var(--hover)}
.badge{display:grid;place-items:center;width:20px;height:20px;border-radius:6px;background:var(--s);color:var(--on)}
.badge svg{width:13px;height:13px}
.fileicon{display:grid;place-items:center;width:20px;height:20px;color:var(--muted)}
.fileicon svg{width:15px;height:15px}
.name{min-width:0;font:400 var(--name)/1.3 var(--mono);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.name .path{color:var(--muted)}
.name .leaf{color:var(--text);font-weight:600}
.files{max-width:var(--wrap);margin:0 auto;padding:.2rem var(--pad) 0}
.files[hidden]{display:none}
.group{margin:0;padding:.3rem 0 .5rem;font:600 var(--label)/1 var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.logfile{background:none;border:0;border-bottom:1px solid var(--line);border-radius:0}
.logfile:hover{border-color:var(--line)}
.logfile:last-child{border-bottom:0}
.logfile summary{grid-template-columns:auto 1fr auto auto;padding:.7rem .3rem}
.logfile summary:hover{background:none;color:var(--text)}
.logfile .panel{border-top:0;padding:0 .3rem .9rem}
.stag{min-width:3.6rem;text-align:right;font:600 var(--label)/1 var(--sans);letter-spacing:.05em;text-transform:uppercase;color:var(--s)}
.dur{min-width:2.8rem;text-align:right;font:400 var(--meta)/1 var(--mono);color:var(--muted);font-variant-numeric:tabular-nums}
.chev{display:block;width:15px;height:15px;color:var(--faint);transition:transform .18s}
details[open]>summary .chev{transform:rotate(90deg)}
.panel{padding:.15rem .9rem .9rem;border-top:1px solid var(--line)}
video{display:block;width:100%;max-width:600px;margin:.9rem 0;border-radius:8px;background:#000;border:1px solid var(--border)}
.log{margin-top:.9rem}
.cap{display:block;margin-bottom:.45rem;font:600 var(--label)/1 var(--sans);letter-spacing:.07em;text-transform:uppercase;color:var(--muted)}
pre{margin:0;white-space:pre-wrap;word-break:break-word;background:var(--bg);border:1px solid var(--line);
 border-radius:8px;padding:.85rem .9rem;font:var(--code)/1.55 var(--mono);color:var(--text);overflow:auto;max-height:30rem}
.muted{margin:.9rem 0 0;color:var(--muted)}
"""

SCRIPT = """
(function(){
 var rows=[].slice.call(document.querySelectorAll('main details[data-status]'));
 var chips=[].slice.call(document.querySelectorAll('.chip'));
 var search=document.querySelector('.search');
 var active='ALL';
 function apply(){
  var q=((search&&search.value)||'').toLowerCase();
  rows.forEach(function(r){
   var okS=active==='ALL'||r.getAttribute('data-status')===active;
   var okQ=!q||r.getAttribute('data-name').toLowerCase().indexOf(q)>=0;
   r.hidden=!(okS&&okQ);
  });
 }
 chips.forEach(function(c){c.addEventListener('click',function(){
  active=c.getAttribute('data-filter');
  chips.forEach(function(x){x.classList.toggle('on',x===c);});
  apply();
 });});
 if(search)search.addEventListener('input',apply);

 var root=document.documentElement,btn=document.querySelector('.themebtn'),modes=['system','light','dark'];
 var ic={system:'<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/>',
  light:'<circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5 5l1.4 1.4M17.6 17.6 19 19M19 5l-1.4 1.4M5 19l1.4-1.4"/>',
  dark:'<path d="M20 13.5A7.5 7.5 0 1 1 10.5 4 6 6 0 0 0 20 13.5z"/>'};
 function setTheme(m){
  if(m==='system')root.removeAttribute('data-theme');else root.setAttribute('data-theme',m);
  var svg=btn&&btn.querySelector('svg');if(svg)svg.innerHTML=ic[m];
  try{localStorage.setItem('oeqa-theme',m);}catch(e){}
 }
 var cur='system';try{cur=localStorage.getItem('oeqa-theme')||'system';}catch(e){}
 if(modes.indexOf(cur)<0)cur='system';setTheme(cur);
 if(btn)btn.addEventListener('click',function(){
  var c=root.getAttribute('data-theme')||'system';
  setTheme(modes[(modes.indexOf(c)+1)%modes.length]);
 });
})();
"""
