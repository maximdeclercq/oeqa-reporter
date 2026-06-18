from pathlib import Path

from oeqa_reporter.report import ORDER, parse_log, render

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_log_extracts_windows_and_body():
    logged = parse_log(FIXTURES / "oe-test.log")
    assert set(logged) == {"demo.DemoTest.test_alpha", "demo.DemoTest.test_beta",
                           "demo.DemoTest.test_gamma"}
    alpha = logged["demo.DemoTest.test_alpha"]
    assert alpha["end"] > alpha["start"]                       # spans its command output
    assert any("echo hello" in line for line in alpha["body"])
    assert all("... ok" not in line for line in alpha["body"])  # result line stripped


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
    assert page.index("test_beta") < page.index("test_alpha")  # failed sorts first
    assert (ev / "summary.txt").read_text().strip().endswith("1 SKIPPED")
