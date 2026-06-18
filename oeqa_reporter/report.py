"""Render an oeqa evidence directory into a self-contained index.html."""
from __future__ import annotations

import html
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

# evidence directory layout; the runner writes these, the report links them
CAPTURE = "capture.mp4"
RUN_LOG = "oe-test.log"
RESULTS = "testresults.json"
SUMMARY = "summary.txt"
INDEX = "index.html"
CLIPS = "clips"
BOOT_CLIP = "boot.mp4"
COLLECTED_LOGS = ("flash.log", "serial.log", "dmesg.log", "journal.log")

BLEED = 4.0                                  # seconds kept around each test window
STAMP = "%Y-%m-%d %H:%M:%S,%f"
LOGLINE = re.compile(r"^(\d{4}-\d\d-\d\d [\d:,]+) - \w+ - \w+ - (.*)")
TESTSTART = re.compile(r"(test_\w+) \(([\w.]+)\)$")
RESULT = re.compile(r"\.\.\. (ok|FAIL|ERROR|skipped)$")
COLOR = {"PASSED": "#1a7f37", "FAILED": "#cf222e", "ERROR": "#bc4c00",
         "SKIPPED": "#9a6700", "BOOT": "#0969da"}
ORDER = {"FAILED": 0, "ERROR": 1, "SKIPPED": 2, "PASSED": 3}


def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


def video_seconds(path):
    out = sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
              "-of", "default=nw=1:nk=1", str(path)])
    return float(out) if out else 0.0


def clip(video, start, end, dest):
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-ss", "%.2f" % max(0.0, start),
                    "-to", "%.2f" % end, "-i", str(video), "-c", "copy", str(dest)],
                   capture_output=True)


def parse_log(path):
    """Map each test id to its {start, end, body} from the timestamped run log."""
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
        elif cur:
            tests[cur]["end"] = when
            if not RESULT.search(text):
                tests[cur]["body"].append(text)
    return tests


def section(title, summary, body):
    open_attr = " open" if summary["status"] in ("FAILED", "ERROR", "BOOT") else ""
    pill = '<span class=pill style="background:%s">%s</span>' % (
        COLOR.get(summary["status"], "#57606a"), summary["status"])
    video = ('<video controls preload=none src="%s"></video>' % summary["clip"]
             if summary.get("clip") else "")
    log = "\n".join(body).strip()
    log_html = "<pre>%s</pre>" % html.escape(log) if log else "<p class=muted>no captured output</p>"
    return ("<details%s><summary>%s <code>%s</code> "
            "<span class=muted>%.1fs</span></summary>%s%s</details>") % (
        open_attr, pill, html.escape(title), summary["dur"], video, log_html)


def render(evidence, title=None):
    """Write index.html into the evidence directory and return its path."""
    ev = Path(evidence)
    title = title or ev.name
    results = next(iter(json.loads((ev / RESULTS).read_text()).values()))["result"]
    logged = parse_log(ev / RUN_LOG)
    video = ev / CAPTURE
    have_video = video.exists() and bool(logged)
    dur = video_seconds(video) if have_video else 0.0
    anchor = max(t["end"] for t in logged.values()) - dur if have_video else 0.0
    first = 0.0
    if have_video:
        (ev / CLIPS).mkdir(exist_ok=True)
        first = min(t["start"] for t in logged.values()) - anchor
        clip(video, 0, first + BLEED, ev / CLIPS / BOOT_CLIP)

    counts, items = {}, []
    for tid, r in results.items():
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        name = tid.split(".")[0] + "." + tid.split(".")[-1]
        body = list(logged.get(tid, {}).get("body", []))
        if len(body) > 120:
            body = ["... (%d earlier log lines omitted)" % (len(body) - 120)] + body[-120:]
        if r.get("log"):
            body += ["", r["log"]]
        clip_src = None
        if have_video and tid in logged:
            clip_src = "%s/%s.mp4" % (CLIPS, name)
            clip(video, logged[tid]["start"] - anchor - BLEED,
                 logged[tid]["end"] - anchor + BLEED, ev / clip_src)
        items.append((ORDER.get(r["status"], 9), logged.get(tid, {}).get("start", 0),
                      section(tid, {"status": r["status"], "dur": r.get("duration", 0.0),
                                    "clip": clip_src}, body)))

    chips = " ".join('<span class=pill style="background:%s">%d %s</span>'
                     % (COLOR.get(k, "#57606a"), v, k.lower()) for k, v in sorted(counts.items()))
    (ev / SUMMARY).write_text(
        ev.name + "\n" + "  ".join("%d %s" % (v, k) for k, v in sorted(counts.items())) + "\n")

    parts = []
    if have_video:
        parts += ['<a href="%s">capture</a>' % CAPTURE,
                  '<a href="%s/%s">boot clip</a>' % (CLIPS, BOOT_CLIP)]
    parts.append('<a href="%s">summary</a>' % SUMMARY)
    for name in COLLECTED_LOGS:
        f = ev / name
        if f.exists() and f.stat().st_size:
            parts.append('<a href="%s">%s</a>' % (name, name))
    links = " &middot; ".join(parts)

    boot = ""
    if have_video:
        boot = section("boot / power-on",
                       {"status": "BOOT", "dur": first + BLEED, "clip": "%s/%s" % (CLIPS, BOOT_CLIP)},
                       ["power-on through the first test; full capture linked above"])
    rows = boot + "\n" + "\n".join(s for _, _, s in sorted(items))
    index = ev / INDEX
    index.write_text(PAGE.format(title=html.escape(title), chips=chips, links=links, rows=rows))
    return index


PAGE = """<!doctype html><meta charset=utf-8><title>{title}</title>
<style>
 body{{font:14px/1.5 system-ui,sans-serif;margin:2rem auto;max-width:60rem;color:#1f2328}}
 header{{position:sticky;top:0;background:#fff;padding:.5rem 0 1rem;border-bottom:1px solid #d0d7de}}
 h1{{font-size:1.1rem;margin:0 0 .5rem}}
 .pill{{color:#fff;padding:.1rem .55rem;border-radius:1rem;font-size:.8rem;font-weight:600}}
 details{{border:1px solid #d0d7de;border-radius:6px;margin:.4rem 0;padding:.3rem .6rem}}
 summary{{cursor:pointer;display:flex;gap:.6rem;align-items:center}}
 summary code{{font-size:.9rem}} .muted{{color:#8c959f}}
 video{{display:block;width:100%;max-width:540px;margin:.6rem 0;border-radius:4px}}
 pre{{white-space:pre-wrap;background:#f6f8fa;padding:.6rem;border-radius:4px;font-size:.8rem;overflow:auto}}
</style>
<header><h1>{title}</h1><div>{chips}</div><div class=muted>{links}</div></header>
{rows}
"""
