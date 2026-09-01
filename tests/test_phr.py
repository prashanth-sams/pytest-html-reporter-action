"""Tests for the action's helper.

Everything the action shows a user is decided here - the counts, the pass
rate, the markdown and the thresholds - so this is where the action is
actually verified. The workflow in .github/workflows/self-test.yml then
checks that the composite wiring around it holds together on a real runner.
"""

import json
import os
import subprocess
import sys

import pytest

from conftest import FIXTURES

import phr


SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "scripts", "phr.py")


def load(name="output.json"):
    return phr.Run.load(os.path.join(FIXTURES, name))


# ---------------------------------------------------------------------------
# reading a run
# ---------------------------------------------------------------------------

def test_counts_come_from_status_list():
    run = load()

    assert run.counts == {
        "pass": 2, "fail": 1, "skip": 1, "error": 1,
        "xpass": 0, "xfail": 0, "rerun": 1,
    }


def test_total_leaves_reruns_out():
    # 2 passed + 1 failed + 1 skipped + 1 errored. The rerun was an attempt at
    # a test already counted, not a sixth test.
    assert load().total == 5


def test_pass_rate_ignores_skips():
    # 2 passed out of 2 passed + 1 failed + 1 errored.
    assert load().pass_rate == 50.0


def test_pass_rate_is_none_when_nothing_decisive_ran():
    run = phr.Run({"status_list": {"skip": "3"}, "status": "PASS"})

    assert run.pass_rate is None


def test_status_is_read_from_the_report():
    assert load().status == "FAIL"
    assert load("output-pass.json").status == "PASS"


def test_status_is_unknown_when_no_report_was_written(tmp_path):
    run = phr.Run.load(str(tmp_path / "nothing.json"))

    assert run.found is False
    assert run.status == "UNKNOWN"


def test_a_malformed_report_is_survivable(tmp_path, capsys):
    broken = tmp_path / "output.json"
    broken.write_text("{not json", encoding="utf-8")

    run = phr.Run.load(str(broken))

    assert run.found is False
    assert "could not be parsed" in capsys.readouterr().out


def test_duration_sums_the_tests():
    # 1.25 + 3.5 + 0.5 + 0.0 + 0.1
    assert load().duration == 5.35


def test_coverage_block_is_passed_through():
    coverage = load().coverage

    assert coverage["percent"] == 87.42
    assert coverage["branch"] is True


def test_no_coverage_block_reads_as_none():
    assert load("output-pass.json").coverage is None


def test_failures_carry_their_suite_and_message():
    failures = load().failures()

    assert [(f["suite"], f["test"], f["status"]) for f in failures] == [
        ("tests/test_login.py", "test_login_bad_password", "FAIL"),
        ("tests/test_cart.py", "test_wishlist", "ERROR"),
    ]
    assert failures[0]["message"] == "AssertionError: expected 200, got 500"
    assert failures[0]["rerun"] == 1


def test_suites_are_ordered_numerically_not_lexically():
    # The plugin keys suites by a stringified index, so "10" must not sort
    # between "1" and "2".
    suites = dict((str(i), {"suite_name": "suite_%d" % i, "tests": {}, "status": {}})
                  for i in range(12))
    run = phr.Run({"content": {"suites": suites}})

    names = [row["name"] for row in run.suite_rows()]

    assert names[:3] == ["suite_0", "suite_1", "suite_2"]
    assert names[-1] == "suite_11"


def test_slowest_drops_the_zero_length_tests():
    slowest = load().slowest(limit=10)

    assert slowest[0]["test"] == "test_login_bad_password"
    assert all(test["duration"] > 0 for test in slowest)


# ---------------------------------------------------------------------------
# path resolution
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path,directory,name", [
    ("report", "report", "pytest_html_report.html"),
    ("./report", "report", "pytest_html_report.html"),
    ("report/", "report", "pytest_html_report.html"),
    ("report/run.html", "report", "run.html"),
    ("run.html", "", "run.html"),
    ("", "", "pytest_html_report.html"),
])
def test_resolve_report_matches_the_plugin(path, directory, name, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    resolved_dir, resolved_name = phr.resolve_report(path)

    assert resolved_name == name
    assert os.path.realpath(resolved_dir) == os.path.realpath(str(tmp_path / directory))


def test_strftime_placeholders_are_expanded():
    expanded = phr.expand_time("reports/%Y/run.html")

    assert "%Y" not in expanded
    assert expanded.startswith("reports/2")


def test_a_percent_that_is_not_a_directive_is_left_alone():
    assert phr.expand_time("reports/100% pass/run.html") == "reports/100% pass/run.html"


def test_a_doubled_percent_becomes_one():
    assert phr.expand_time("reports/100%%Y") != "reports/100%%Y"
    assert phr.expand_time("100%%pass") == "100%pass"


# ---------------------------------------------------------------------------
# the pytest command
# ---------------------------------------------------------------------------

def test_args_start_with_the_resolved_report_path():
    args = phr.build_args({}, "report")

    assert args[0] == "--html-report=report"


def test_a_browser_is_never_opened_unless_it_is_asked_for():
    # Not left to the plugin's "auto": the CLI beats the ini, so a repo whose
    # pytest.ini says `report_open = always` would open a console browser on
    # the runner and wait for it.
    assert "--report-open=none" in phr.build_args({}, "report")


def test_report_open_is_passed_through_when_it_is_asked_for():
    args = phr.build_args({"PHR_REPORT_OPEN": "always"}, "report")

    assert "--report-open=always" in args
    assert "--report-open=none" not in args


def test_single_valued_options_become_flags():
    args = phr.build_args({
        "PHR_TITLE": "Nightly",
        "PHR_ENVIRONMENT": "staging",
        "PHR_ARCHIVE_COUNT": "7",
        "PHR_REPORT_LOGS": "failed",
    }, "report")

    assert "--title=Nightly" in args
    assert "--environment=staging" in args
    assert "--archive-count=7" in args
    assert "--report-logs=failed" in args


def test_an_empty_input_adds_no_flag():
    args = phr.build_args({"PHR_TITLE": "", "PHR_ENVIRONMENT": "   "}, "report")

    assert not any(arg.startswith("--title") for arg in args)
    assert not any(arg.startswith("--environment") for arg in args)


def test_multiline_inputs_repeat_their_flag():
    args = phr.build_args({
        "PHR_BUILD_INFO": "branch=main\n# a comment\nteam=payments\n\n",
        "PHR_REPORT_LINKS": "Coverage=htmlcov/index.html",
    }, "report")

    assert args.count("--build-info=branch=main") == 1
    assert "--build-info=team=payments" in args
    assert not any("comment" in arg for arg in args)
    assert "--report-link=Coverage=htmlcov/index.html" in args


def test_test_paths_are_passed_one_per_line():
    args = phr.build_args({"PHR_TESTS": "tests/unit\ntests/api\n"}, "report")

    assert args[-2:] == ["tests/unit", "tests/api"]


def test_extra_args_are_split_like_a_shell_would():
    args = phr.build_args({"PHR_PYTEST_ARGS": '-n auto -m "not slow"'}, "report")

    assert args[-4:] == ["-n", "auto", "-m", "not slow"]


def test_a_test_path_holding_a_space_stays_one_argument():
    args = phr.build_args({"PHR_TESTS": "tests/my tests"}, "report")

    assert args[-1] == "tests/my tests"


# ---------------------------------------------------------------------------
# thresholds
# ---------------------------------------------------------------------------

class Options(object):
    def __init__(self, **kw):
        self.fail_on_error = kw.get("fail_on_error", "true")
        self.min_pass_rate = kw.get("min_pass_rate", "")
        self.min_coverage = kw.get("min_coverage", "")
        self.fail_on_empty = kw.get("fail_on_empty", "true")


def test_a_green_run_passes_the_gate():
    ok, reasons = phr.gate(load("output-pass.json"), 0, Options())

    assert ok is True
    assert reasons == []


def test_a_failing_pytest_fails_the_gate():
    ok, reasons = phr.gate(load(), 1, Options())

    assert ok is False
    assert "exited with code 1" in reasons[0]


def test_fail_on_error_false_lets_a_failing_run_through():
    ok, reasons = phr.gate(load(), 1, Options(fail_on_error="false"))

    assert ok is True


@pytest.mark.parametrize("code,explanation", [
    (1, "tests failed"),
    (2, "the run was interrupted"),
    (4, "a usage error"),
])
def test_an_exit_code_is_explained_not_just_quoted(code, explanation):
    ok, reasons = phr.gate(load("output-pass.json"), code, Options())

    assert ok is False
    assert reasons[0].startswith("pytest exited with code %d" % code)
    assert explanation in reasons[0]


def test_a_clean_exit_with_no_report_is_a_failure(tmp_path):
    run = phr.Run.load(str(tmp_path / "output.json"))

    ok, reasons = phr.gate(run, 0, Options())

    assert ok is False
    assert "wrote no output.json" in reasons[0]


def test_a_pass_rate_below_the_threshold_fails():
    ok, reasons = phr.gate(load(), 0, Options(min_pass_rate="80"))

    assert ok is False
    assert "pass rate 50% is below the required 80%" in reasons[-1]


def test_a_pass_rate_above_the_threshold_passes():
    ok, _ = phr.gate(load("output-pass.json"), 0, Options(min_pass_rate="80"))

    assert ok is True


def test_coverage_below_the_threshold_fails():
    ok, reasons = phr.gate(load(), 0, Options(min_coverage="90"))

    assert ok is False
    assert "coverage 87.42% is below the required 90%" in reasons[-1]


def test_a_coverage_threshold_with_no_coverage_measured_fails_loudly():
    ok, reasons = phr.gate(load("output-pass.json"), 0, Options(min_coverage="80"))

    assert ok is False
    assert "produced no coverage data" in reasons[-1]


def test_a_nonsense_threshold_warns_rather_than_gating(capsys):
    ok, reasons = phr.gate(load("output-pass.json"), 0, Options(min_coverage="high"))

    assert ok is True
    assert "::warning" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# markdown
# ---------------------------------------------------------------------------

def render(name="output.json", **context):
    context.setdefault("title", "pytest-html-reporter")
    return phr.render(load(name), context)


def test_the_summary_leads_with_the_status():
    assert render().splitlines()[0] == "## ❌ pytest-html-reporter"
    assert render("output-pass.json").splitlines()[0] == "## ✅ pytest-html-reporter"


def test_the_headline_counts_tests_and_suites():
    assert "5 tests across 2 suites" in render()


def test_the_headline_carries_the_wall_clock_when_it_is_known():
    assert "in 1m 14s" in render(wall_clock=74)
    assert "in " not in render().splitlines()[2]


def test_every_failure_is_listed():
    markdown = render()

    assert "test_login_bad_password" in markdown
    assert "test_wishlist" in markdown
    assert "AssertionError: expected 200, got 500" in markdown


def test_failures_beyond_the_limit_are_counted_not_dropped_silently():
    markdown = render(failure_limit=1)

    assert "test_login_bad_password" in markdown
    assert "1 further failure not listed here" in markdown


def test_a_message_cannot_break_out_of_its_code_fence():
    run = phr.Run({
        "status": "FAIL",
        "status_list": {"fail": "1"},
        "total_suite": 1,
        "content": {"suites": {"0": {
            "suite_name": "tests/test_x.py",
            "status": {"total_fail": 1},
            "tests": {"0": {"status": "FAIL", "test_name": "test_x",
                            "message": "look:\n```\nnot a fence\n```", "rerun": "0",
                            "duration": 0.0}},
        }}},
    })

    markdown = phr.render(run, {"title": "t"})

    # One opening and one closing fence for the one failure, and no stray
    # pair smuggled in by the message.
    assert markdown.count("```") == 2


def test_a_long_message_is_trimmed():
    run = phr.Run({
        "status": "FAIL",
        "status_list": {"fail": "1"},
        "total_suite": 1,
        "content": {"suites": {"0": {
            "suite_name": "s", "status": {"total_fail": 1},
            "tests": {"0": {"status": "FAIL", "test_name": "t",
                            "message": "x" * 9000, "rerun": "0", "duration": 0.0}},
        }}},
    })

    markdown = phr.render(run, {"title": "t"})

    assert "trimmed" in markdown
    assert len(markdown) < 6000


def test_a_name_holding_a_backtick_stays_inside_its_code_span():
    assert phr._code("a`b") == "``a`b``"
    assert phr._code("`b") == "`` `b ``"


def test_coverage_is_shown_when_the_run_measured_it():
    assert "**Coverage** 87.42% (branch)" in render()
    assert "Coverage" not in render("output-pass.json")


def test_a_single_suite_run_skips_the_suites_table():
    assert "### Suites" not in render("output-pass.json")
    assert "### Suites" in render()


def test_links_are_shown_when_they_are_known():
    markdown = render(artifact_url="https://example.test/artifact",
                      pages_url="https://example.test/report")

    assert "[Open the report](https://example.test/report)" in markdown
    assert "[Download the artifact](https://example.test/artifact)" in markdown


def test_a_missing_report_explains_itself(tmp_path):
    markdown = phr.render(phr.Run.load(str(tmp_path / "output.json")),
                          {"title": "t", "report_dir": str(tmp_path)})

    assert "No `output.json` was written" in markdown
    assert "not installed" in markdown


# ---------------------------------------------------------------------------
# the GitHub Actions plumbing
# ---------------------------------------------------------------------------

def test_outputs_are_appended_to_github_output(tmp_path, monkeypatch):
    target = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(target))

    phr.write_output("status", "PASS")
    phr.write_output("total", 5)

    assert target.read_text(encoding="utf-8") == "status=PASS\ntotal=5\n"


def test_a_multiline_output_uses_a_heredoc(tmp_path, monkeypatch):
    target = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(target))

    phr.write_output("summary", "line one\nline two")

    written = target.read_text(encoding="utf-8")

    assert written.startswith("summary<<phr_summary_")
    assert "line one\nline two\n" in written


def test_a_payload_saying_eof_cannot_close_the_block_early(tmp_path, monkeypatch):
    target = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(target))

    phr.write_output("summary", "EOF\nstill the summary")

    written = target.read_text(encoding="utf-8")
    delimiter = written.split("<<", 1)[1].splitlines()[0]

    assert written.count(delimiter) == 2


def test_workflow_command_messages_escape_their_newlines(capsys):
    phr.fail("first\nsecond 100%")

    assert capsys.readouterr().out.strip() == (
        "::error title=pytest-html-reporter::first%0Asecond 100%25")


# ---------------------------------------------------------------------------
# the CLI, end to end
# ---------------------------------------------------------------------------

def run_cli(args, env=None):
    environment = dict(os.environ)
    environment.update(env or {})

    return subprocess.run([sys.executable, SCRIPT] + args, env=environment,
                          capture_output=True, text=True)


def test_resolve_writes_the_paths_it_settled_on(tmp_path, monkeypatch):
    output = tmp_path / "out"
    result = run_cli(["resolve", "--path", str(tmp_path / "report")],
                     {"GITHUB_OUTPUT": str(output)})

    assert result.returncode == 0

    written = dict(line.split("=", 1) for line in
                   output.read_text(encoding="utf-8").strip().splitlines())

    assert written["report-file"].endswith("pytest_html_report.html")
    assert written["json-path"].endswith("output.json")
    assert os.path.isdir(written["report-dir"])


def test_summarize_reports_and_gates_in_one_pass(tmp_path):
    output = tmp_path / "out"
    summary = tmp_path / "summary.md"
    comment = tmp_path / "comment.md"

    result = run_cli(
        ["summarize", "--json", os.path.join(FIXTURES, "output.json"),
         "--exit-code", "1", "--comment-body", str(comment)],
        {"GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary)})

    assert result.returncode == 1, result.stdout

    written = output.read_text(encoding="utf-8")

    assert "status=FAIL" in written
    assert "pass-rate=50" in written
    assert "gate-passed=false" in written
    assert "## ❌" in summary.read_text(encoding="utf-8")
    assert comment.read_text(encoding="utf-8").startswith(phr.COMMENT_MARKER)


def test_summarize_leaves_a_green_run_green(tmp_path):
    output = tmp_path / "out"

    result = run_cli(
        ["summarize", "--json", os.path.join(FIXTURES, "output-pass.json")],
        {"GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(tmp_path / "s.md")})

    assert result.returncode == 0
    assert "gate-passed=true" in output.read_text(encoding="utf-8")


def test_args_are_written_nul_separated(tmp_path):
    argfile = tmp_path / "args"

    result = run_cli(["args", "--html-report", "report", "--out", str(argfile)],
                     {"PHR_TITLE": "Nightly", "PHR_TESTS": "tests/unit"})

    assert result.returncode == 0

    args = argfile.read_bytes().split(b"\0")[:-1]

    assert args[0] == b"--html-report=report"
    assert b"--title=Nightly" in args
    assert args[-1] == b"tests/unit"


# ---------------------------------------------------------------------------
# the sharp edges the plugin has, surfaced rather than hidden
# ---------------------------------------------------------------------------

def test_a_pipe_in_a_name_cannot_break_the_table():
    run = phr.Run({
        "status": "FAIL", "status_list": {"fail": "1"}, "total_suite": 2,
        "content": {"suites": {
            "0": {"suite_name": "tests/a|b.py", "status": {"total_fail": 1},
                  "tests": {"0": {"status": "FAIL", "test_name": "t",
                                  "message": "", "rerun": "0", "duration": 1.0}}},
            "1": {"suite_name": "tests/c.py", "status": {"total_pass": 1}, "tests": {}},
        }},
    })

    markdown = phr.render(run, {"title": "t"})
    row = [line for line in markdown.splitlines()
           if line.startswith("| ") and "b.py" in line][0]

    # GFM splits a table row on unescaped pipes, inside a code span as much
    # as outside it, so the escape is what keeps this one row a row.
    assert "a\\|b.py" in row
    assert row.replace("\\|", "").count("|") == 6  # five cells, six delimiters


def test_a_test_name_cannot_smuggle_in_a_workflow_command():
    defanged = phr._defang("::set-output name=x::y")

    assert not defanged.startswith("::")
    assert "set-output" in defanged


def test_a_windows_path_is_normalised_before_the_plugin_sees_it(monkeypatch):
    monkeypatch.setattr(phr.os, "sep", "\\")
    monkeypatch.setattr(phr.os, "altsep", "/")

    assert phr.normalise("out\\reports\\run.html") == "out/reports/run.html"


def test_a_posix_path_keeps_its_backslashes(monkeypatch):
    monkeypatch.setattr(phr.os, "sep", "/")
    monkeypatch.setattr(phr.os, "altsep", None)

    assert phr.normalise("odd\\name") == "odd\\name"


def test_a_folder_named_with_html_is_warned_about(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    class Options(object):
        path = "out.html_v2/report.html"

    phr.cmd_resolve(Options())

    assert "reads that as 'no folder'" in capsys.readouterr().out


def test_the_job_summary_is_trimmed_to_fit(tmp_path, monkeypatch):
    target = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(target))

    phr.write_summary("x" * (phr.SUMMARY_LIMIT + 5000))

    written = target.read_text(encoding="utf-8")

    assert len(written.encode("utf-8")) < phr.SUMMARY_LIMIT + 200
    assert "Trimmed to fit" in written


# ---------------------------------------------------------------------------
# what the installed plugin actually supports
# ---------------------------------------------------------------------------

HELP = """
  --html-report=PATH    path to generate html report
  --title=TITLE         customize report title
  --archive-count=ARCHIVE_COUNT
                        set maximum build count
"""


def test_a_flag_the_installed_plugin_lacks_is_left_out(capsys):
    args = phr.build_args({"PHR_TITLE": "T", "PHR_REPORT_STEPS": "failed"},
                          "report", HELP)

    assert "--title=T" in args
    assert not any(arg.startswith("--report-steps") for arg in args)
    assert "--report-steps" in capsys.readouterr().out


def test_a_flag_the_plugin_has_is_kept():
    args = phr.build_args({"PHR_ARCHIVE_COUNT": "7"}, "report", HELP)

    assert "--archive-count=7" in args


def test_nothing_is_filtered_when_the_probe_failed():
    # Dropping every flag because `pytest --help` could not be read would be
    # worse than passing them and letting pytest say so.
    args = phr.build_args({"PHR_REPORT_STEPS": "failed"}, "report", "")

    assert "--report-steps=failed" in args


def test_a_repeated_flag_is_dropped_as_a_whole(capsys):
    args = phr.build_args({"PHR_BUILD_INFO": "a=1\nb=2"}, "report", HELP)

    assert not any(arg.startswith("--build-info") for arg in args)
    assert "--build-info" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# readying a restored history
# ---------------------------------------------------------------------------

class Prime(object):
    def __init__(self, directory, name="pytest_html_report.html"):
        self.report_dir = str(directory)
        self.report_name = name


def test_a_placeholder_is_stood_in_so_the_last_build_is_archived(tmp_path):
    # The plugin archives the previous build by rotating its output.json, and
    # only when the previous report file is on disk. A restored cache has the
    # json and no report, so without this every build would replace the last.
    (tmp_path / "output.json").write_text("{}", encoding="utf-8")

    phr.cmd_prime(Prime(tmp_path))

    assert (tmp_path / "pytest_html_report.html").exists()


def test_no_placeholder_is_invented_for_a_first_run(tmp_path):
    phr.cmd_prime(Prime(tmp_path))

    assert not (tmp_path / "pytest_html_report.html").exists()


def test_an_unreadable_archived_build_is_set_aside(tmp_path, capsys):
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "output_1.json").write_text('{"status": "PASS"}', encoding="utf-8")
    (archive / "output_2.json").write_text("{truncated", encoding="utf-8")
    (archive / "output_3.json").write_text('{"status": "MAYBE"}', encoding="utf-8")

    phr.cmd_prime(Prime(tmp_path))

    kept = sorted(path.name for path in archive.iterdir())

    assert kept == ["output_1.json", "output_2.json.unreadable",
                    "output_3.json.unreadable"]
    assert "could not be read" in capsys.readouterr().out


def test_stale_screenshots_are_cleared(tmp_path):
    shots = tmp_path / "pytest_screenshots"
    shots.mkdir()
    (shots / "old.png").write_bytes(b"")

    phr.cmd_prime(Prime(tmp_path))

    assert not shots.exists()


# ---------------------------------------------------------------------------
# saying why a run went wrong
# ---------------------------------------------------------------------------

def test_collecting_nothing_fails_by_default():
    ok, reasons = phr.gate(load("output-pass.json"), 5, Options())

    assert ok is False
    assert "collected no tests" in reasons[0]


def test_collecting_nothing_can_be_allowed():
    ok, _ = phr.gate(load("output-pass.json"), 5, Options(fail_on_empty="false"))

    assert ok is True


def test_a_usage_error_is_quoted_back_from_the_log(tmp_path):
    log = tmp_path / "pytest.log"
    log.write_text("collecting ...\n"
                   "ERROR: --archive-since takes a date, YYYY-MM-DD, not 'yesterday'\n",
                   encoding="utf-8")

    lines = phr.usage_errors(str(log))

    assert len(lines) == 1
    assert "archive-since" in lines[0]


def test_an_unrecognised_flag_is_recognised_as_a_usage_error(tmp_path):
    log = tmp_path / "pytest.log"
    log.write_text("pytest: error: unrecognized arguments: --report-steps=all\n",
                   encoding="utf-8")

    assert phr.usage_errors(str(log))


def test_a_comment_body_is_trimmed_harder_than_the_summary(tmp_path):
    body = tmp_path / "comment.md"

    class Options(object):
        json = os.path.join(FIXTURES, "output.json")
        title = "t"
        exit_code = "0"
        wall_clock = ""
        failure_limit = 500
        suite_limit = 500
        slowest_limit = 5
        job_summary = "false"
        comment_body = str(body)
        artifact_url = pages_url = run_url = ""
        fail_on_error = "false"
        fail_on_empty = "false"
        min_pass_rate = min_coverage = coverage_file = pytest_log = ""

    phr.cmd_summarize(Options())

    assert len(body.read_text(encoding="utf-8")) <= phr.COMMENT_LIMIT + 200
