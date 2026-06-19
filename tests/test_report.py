from pathlib import Path

from oeqa_reporter.report import ORDER, parse_log, render

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_log_closes_window_at_result():
    logged = parse_log(FIXTURES / "oe-test.log")
    assert set(logged) == {"demo.DemoTest.test_alpha", "demo.DemoTest.test_beta",
                           "demo.DemoTest.test_gamma"}
    beta = logged["demo.DemoTest.test_beta"]
    assert beta["end"] > beta["start"]                         # window spans start -> result
    # the inline post-result traceback and the end-of-run "FAIL: test_beta (...)" summary
    # block both stay out of the window; testresults.json owns the traceback, so rendering
    # the scraped body plus the json log must not double up
    assert beta["body"] == []
    # the skipped result line and its echoed reason are not scraped as output either
    assert logged["demo.DemoTest.test_gamma"]["body"] == []


def test_order_puts_failures_before_passes():
    statuses = ["PASSED", "SKIPPED", "ERROR", "FAILED"]
    assert sorted(statuses, key=ORDER.__getitem__) == ["FAILED", "ERROR", "SKIPPED", "PASSED"]


def test_render_without_video(tmp_path):
    ev = tmp_path / "evidence"
    ev.mkdir()
    (ev / "oe-test.log").write_text((FIXTURES / "oe-test.log").read_text())
    (ev / "testresults.json").write_text((FIXTURES / "testresults.json").read_text())

    index = render(ev, title="demo run")
    page = index.read_text()

    assert index.name == "index.html"
    assert "<title>demo run</title>" in page
    assert "1 passed" in page and "1 failed" in page and "1 skipped" in page
    # order is checked within the test list; the files section also embeds the raw run log
    rows = page.split("<main>")[1].split("</main>")[0]
    assert rows.index("test_beta") < rows.index("test_alpha")  # failed sorts first
    assert "ltpresult" not in rows  # status-less result blobs are skipped, not rendered
    assert (ev / "summary.txt").read_text().strip().endswith("1 SKIPPED")
