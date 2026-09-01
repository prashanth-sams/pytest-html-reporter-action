#!/usr/bin/env python3
"""Helper CLI for the pytest-html-reporter GitHub Action.

The action shells out to this module three times:

    phr.py resolve    work out where the report and its output.json will land
    phr.py args       turn the action's inputs into a pytest argument list
    phr.py summarize  read output.json and produce the outputs, the job
                      summary, the PR comment body and the threshold verdict

Everything here is stdlib-only and runs on the Python the workflow already
set up, so the action adds no dependency of its own beyond the plugin.
"""

import argparse
import binascii
import json
import os
import re
import sys

__version__ = "1.0.0"

# Written by the plugin next to the report; the action never guesses at it.
JSON_NAME = "output.json"
ARCHIVE_DIR = "archive"
SCREENSHOT_DIR = "pytest_screenshots"
DEFAULT_REPORT_NAME = "pytest_html_report.html"

# The plugin's own default when --html-report is not given is the working
# directory. The action defaults to ./report instead - a CI job wants the
# report, its output.json and its archive in one uploadable folder - and says
# so in the docs rather than pretending the two defaults agree.
COUNT_KEYS = ("pass", "fail", "skip", "error", "xpass", "xfail", "rerun")

STATUS_LABELS = (
    ("pass", "Passed", "✅"),
    ("fail", "Failed", "❌"),
    ("error", "Error", "\U0001f6a8"),
    ("skip", "Skipped", "⏭️"),
    ("xpass", "xPassed", "❗"),
    ("xfail", "xFailed", "\U0001f7e1"),
    ("rerun", "Rerun", "\U0001f501"),
)

COMMENT_MARKER = "<!-- pytest-html-reporter-action -->"

# What pytest means by the number it exits with. Code 4 is the one worth
# spelling out: it is a usage error, so it is the action's own inputs - or a
# value in the repo's pytest.ini - that pytest refused, not a failing test.
EXIT_CODES = {
    1: "tests failed",
    2: "the run was interrupted",
    3: "an internal error",
    4: "a usage error: pytest refused an argument. Check this action's report "
       "inputs and pytest-args, and the report keys in your pytest.ini - the "
       "message pytest printed names the flag, which an ini key shares",
    5: "no tests were collected",
}


# ---------------------------------------------------------------------------
# path resolution
# ---------------------------------------------------------------------------

def expand_time(path):
    """Expand strftime placeholders the way the plugin does.

    The plugin's own ``expand_time`` is used when it can be imported, so that
    the action and the plugin can never disagree about where the report went.
    The fallback matters only when the resolve step runs before the plugin is
    installed, and it follows the same rule: a % that does not introduce a
    directive is left alone, so a path holding "100% pass" survives.
    """
    if "%" not in path:
        return path

    try:
        from pytest_html_reporter.util import expand_time as plugin_expand

        return plugin_expand(path)
    except Exception:
        pass

    import re
    from datetime import datetime

    directives = "aAbBcdfGHIjmMpSuUVwWxXyYzZ"
    now = datetime.now()

    def expand(match):
        directive = match.group(1)
        if directive == "%":
            return "%"
        if directive in directives:
            return now.strftime("%" + directive)
        return match.group(0)

    return re.sub("%(.)", expand, path, flags=re.DOTALL)


def normalise(path):
    """Backslashes to forward slashes, on the platform where that is a separator.

    The plugin splits a --html-report value on "/" and nothing else, so on
    Windows `out\\run.html` would be read as one long file name in the
    current directory. Normalising here, and handing pytest the normalised
    value, keeps the action and the plugin looking at the same place. A
    backslash is a legal character in a POSIX file name, so this only
    happens where the separator really is one.
    """
    if os.sep == "\\" or os.altsep == "/":
        return path.replace("\\", "/")

    return path


def resolve_report(path):
    """(report_dir, report_filename) for a --html-report value.

    A faithful copy of ``HTMLReporter.report_path``: the value names a file
    when it holds ".html" anywhere, and a directory otherwise.
    """
    path = path.strip() or "."

    if ".html" in path:
        head = path.rsplit("/", 1)[0]
        base = "." if ".html" in head else head
        if base == "":
            base = "."
        base = os.path.abspath(os.path.expanduser(os.path.expandvars(base)))
        return base, path.split("/")[-1]

    base = os.path.abspath(os.path.expanduser(os.path.expandvars(path)))
    return base, DEFAULT_REPORT_NAME


# ---------------------------------------------------------------------------
# GitHub Actions plumbing
# ---------------------------------------------------------------------------

def write_output(name, value):
    """Append one output to $GITHUB_OUTPUT, or print it when running locally."""
    value = "" if value is None else str(value)
    target = os.environ.get("GITHUB_OUTPUT")

    if not target:
        sys.stdout.write("%s=%s\n" % (name, value))
        return

    with open(target, "a", encoding="utf-8") as handle:
        if "\n" in value:
            # A delimiter the payload cannot contain, so a test name carrying
            # the word EOF cannot end the block early.
            delimiter = "phr_%s_%s" % (name.replace("-", "_"),
                                       binascii.hexlify(os.urandom(8)).decode())
            handle.write("%s<<%s\n%s\n%s\n" % (name, delimiter, value, delimiter))
        else:
            handle.write("%s=%s\n" % (name, value))


# The job summary is capped at 1MiB. Well under it, and truncation says so.
SUMMARY_LIMIT = 900 * 1024

# A GitHub issue comment is capped at 65536 characters, and the API answers a
# longer one with a 422 rather than trimming it.
COMMENT_LIMIT = 60000


def trim(markdown, limit, where):
    """`markdown`, cut to fit, saying so where it was cut."""
    if len(markdown.encode("utf-8")) <= limit:
        return markdown

    tail = ("\n\n_Trimmed to fit %s. The full report is in the artifact._\n" % where)
    room = limit - len(tail.encode("utf-8"))

    return markdown.encode("utf-8")[:room].decode("utf-8", "ignore") + tail


def write_summary(markdown):
    """Append to the job summary, when there is one to append to."""
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return False

    markdown = trim(markdown, SUMMARY_LIMIT, "the job summary")

    with open(target, "a", encoding="utf-8") as handle:
        handle.write(markdown.rstrip() + "\n")

    return True


def notice(message):
    sys.stdout.write("::notice title=pytest-html-reporter::%s\n" % _oneline(message))


def warn(message):
    sys.stdout.write("::warning title=pytest-html-reporter::%s\n" % _oneline(message))


def fail(message):
    sys.stdout.write("::error title=pytest-html-reporter::%s\n" % _oneline(message))


def _oneline(message):
    return str(message).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


# ---------------------------------------------------------------------------
# reading the run
# ---------------------------------------------------------------------------

class Run(object):
    """One build, read out of the plugin's output.json."""

    def __init__(self, data=None, json_path=None):
        self.data = data or {}
        self.json_path = json_path
        self.found = data is not None

    @classmethod
    def load(cls, json_path):
        try:
            with open(json_path, encoding="utf-8") as handle:
                return cls(json.load(handle), json_path)
        except (IOError, OSError):
            return cls(None, json_path)
        except ValueError as error:
            warn("%s could not be parsed as JSON: %s" % (json_path, error))
            return cls(None, json_path)

    # -- headline ---------------------------------------------------------

    @property
    def counts(self):
        raw = self.data.get("status_list") or {}
        return dict((key, _int(raw.get(key))) for key in COUNT_KEYS)

    @property
    def total(self):
        """Tests executed. Reruns are attempts, not tests, so they are out."""
        counts = self.counts
        return sum(counts[key] for key in COUNT_KEYS if key != "rerun")

    @property
    def suites(self):
        return _int(self.data.get("total_suite"))

    @property
    def status(self):
        """PASS or FAIL, as the plugin decided it."""
        value = str(self.data.get("status") or "").upper()
        if value in ("PASS", "FAIL"):
            return value

        counts = self.counts
        if not self.found:
            return "UNKNOWN"

        return "FAIL" if counts["fail"] or counts["error"] else "PASS"

    @property
    def pass_rate(self):
        """passed / (passed + failed + errored), as a percentage.

        Skipped, xfailed and xpassed tests are left out of both halves: none
        of them is a pass-or-fail signal, and counting them silently moves a
        threshold somebody set. None when nothing decisive ran.
        """
        counts = self.counts
        decisive = counts["pass"] + counts["fail"] + counts["error"]
        if decisive == 0:
            return None

        return round(100.0 * counts["pass"] / decisive, 2)

    @property
    def coverage(self):
        block = self.data.get("coverage")
        return block if isinstance(block, dict) else None

    @property
    def duration(self):
        """Summed test durations. Wall clock is measured by the action itself."""
        total = 0.0
        for suite in self._suites():
            for test in (suite.get("tests") or {}).values():
                total += _float(test.get("duration"))

        return round(total, 2)

    # -- detail -----------------------------------------------------------

    def _suites(self):
        suites = ((self.data.get("content") or {}).get("suites")) or {}
        if isinstance(suites, dict):
            # The keys are stringified indices; sort numerically so the
            # report and the summary list the suites in the same order.
            return [suites[key] for key in sorted(suites, key=_sort_key)]

        return list(suites)

    def suite_rows(self):
        rows = []
        for suite in self._suites():
            status = suite.get("status") or {}
            rows.append({
                "name": str(suite.get("suite_name") or "unnamed"),
                "counts": dict(
                    (key, _int(status.get("total_" + key))) for key in COUNT_KEYS
                ),
            })

        return rows

    def failures(self):
        """Every failed or errored test, in report order."""
        out = []
        for suite in self._suites():
            name = str(suite.get("suite_name") or "unnamed")
            tests = suite.get("tests") or {}
            keys = sorted(tests, key=_sort_key) if isinstance(tests, dict) else range(len(tests))
            for key in keys:
                test = tests[key]
                status = str(test.get("status") or "").upper()
                if status in ("FAIL", "ERROR"):
                    out.append({
                        "suite": name,
                        "test": str(test.get("test_name") or "unnamed"),
                        "status": status,
                        "message": str(test.get("message") or "").strip(),
                        "rerun": _int(test.get("rerun")),
                        "duration": _float(test.get("duration")),
                    })

        return out

    def slowest(self, limit=5):
        tests = []
        for suite in self._suites():
            name = str(suite.get("suite_name") or "unnamed")
            for test in (suite.get("tests") or {}).values():
                # A skipped test's duration is the cost of deciding to skip
                # it, which is nobody's idea of a slow test.
                if str(test.get("status") or "").upper() == "SKIP":
                    continue

                tests.append({
                    "suite": name,
                    "test": str(test.get("test_name") or "unnamed"),
                    "duration": _float(test.get("duration")),
                })

        tests.sort(key=lambda item: item["duration"], reverse=True)
        return [test for test in tests[:limit] if test["duration"] > 0]


def _int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _sort_key(value):
    try:
        return (0, int(value), "")
    except (TypeError, ValueError):
        return (1, 0, str(value))


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def render(run, context):
    """The markdown shown in the job summary and posted as the PR comment."""
    lines = []
    icon = {"PASS": "✅", "FAIL": "❌"}.get(run.status, "❓")
    title = context.get("title") or "pytest-html-reporter"

    lines.append("## %s %s" % (icon, title))
    lines.append("")

    if not run.found:
        lines.append(
            "No %s was written to %s, so there is nothing to report. Either pytest "
            "failed before any test ran - a collection error, a bad argument, a "
            "missing dependency - or the plugin was not installed in the "
            "environment that ran the tests. The pytest log above says which."
            % (_code(JSON_NAME), _code(context.get("report_dir") or ".")))
        return "\n".join(lines) + "\n"

    lines.append(_headline(run, context))
    lines.append("")
    lines.append(_counts_table(run))
    lines.append("")

    coverage = _coverage_line(run)
    if coverage:
        lines.append(coverage)
        lines.append("")

    failures = run.failures()
    if failures:
        lines.append(_failures(failures, context.get("failure_limit", 10)))
        lines.append("")

    rows = run.suite_rows()
    if len(rows) > 1:
        lines.append(_suites_table(rows, context.get("suite_limit", 20)))
        lines.append("")

    slowest = run.slowest(context.get("slowest_limit", 5))
    if slowest:
        lines.append(_slowest(slowest))
        lines.append("")

    links = _links(context)
    if links:
        lines.append(links)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _headline(run, context):
    counts = run.counts
    parts = ["**%s**" % run.status]

    scope = "%s %s across %s %s" % (
        run.total, _plural(run.total, "test"),
        run.suites, _plural(run.suites, "suite"))

    wall = context.get("wall_clock")
    if wall:
        scope += " in %s" % _seconds(wall)

    parts.append(scope)

    rate = run.pass_rate
    if rate is not None:
        parts.append("pass rate %s%%" % _trim(rate))

    if counts["rerun"]:
        parts.append("%s %s" % (counts["rerun"], _plural(counts["rerun"], "rerun")))

    return " · ".join(parts)


def _counts_table(run):
    counts = run.counts
    shown = [item for item in STATUS_LABELS if counts[item[0]] or item[0] in ("pass", "fail")]

    header = "| " + " | ".join("%s %s" % (icon, label) for _, label, icon in shown) + " |"
    rule = "|" + "|".join([" ---: "] * len(shown)) + "|"
    values = "| " + " | ".join(str(counts[key]) for key, _, _ in shown) + " |"

    return "\n".join([header, rule, values])


def _coverage_line(run):
    coverage = run.coverage
    if not coverage:
        return ""

    percent = _float(coverage.get("percent"))
    kind = "branch" if coverage.get("branch") else "line"
    covered = _int(coverage.get("covered"))
    statements = _int(coverage.get("statements"))

    return "**Coverage** %s%% (%s) · %s of %s statements covered, %s missing" % (
        _trim(percent), kind, covered, statements, _int(coverage.get("missing")))


def _failures(failures, limit):
    limit = max(0, int(limit or 0)) or len(failures)
    lines = ["### Failures"]

    for failure in failures[:limit]:
        heading = _defang("%s › %s" % (failure["suite"], failure["test"]))
        if failure["status"] == "ERROR":
            heading = "\U0001f6a8 " + heading
        if failure["rerun"]:
            heading += " (rerun %s×)" % failure["rerun"]

        lines.append("")
        lines.append("<details><summary>%s</summary>" % _escape(heading))
        lines.append("")
        lines.append("```text")
        lines.append(_fence_safe(failure["message"] or "No message was captured."))
        lines.append("```")
        lines.append("")
        lines.append("</details>")

    dropped = len(failures) - limit
    if dropped > 0:
        lines.append("")
        lines.append("_%s further %s not listed here - the full report has them._"
                     % (dropped, _plural(dropped, "failure")))

    return "\n".join(lines)


def _suites_table(rows, limit):
    limit = max(0, int(limit or 0)) or len(rows)
    ordered = sorted(rows, key=lambda row: (
        -(row["counts"]["fail"] + row["counts"]["error"]), row["name"]))

    lines = ["### Suites", "",
             "| Suite | ✅ | ❌ | \U0001f6a8 | ⏭️ |",
             "| --- | ---: | ---: | ---: | ---: |"]

    for row in ordered[:limit]:
        counts = row["counts"]
        lines.append("| %s | %s | %s | %s | %s |" % (
            _cell(row["name"]), counts["pass"], counts["fail"],
            counts["error"], counts["skip"]))

    dropped = len(ordered) - limit
    if dropped > 0:
        lines.append("")
        lines.append("_%s further %s not listed here._" % (dropped, _plural(dropped, "suite")))

    return "\n".join(lines)


def _slowest(tests):
    lines = ["<details><summary>Slowest tests</summary>", "",
             "| Test | Duration |", "| --- | ---: |"]

    for test in tests:
        lines.append("| %s › %s | %s |" % (
            _cell(test["suite"]), _cell(test["test"]), _seconds(test["duration"])))

    lines.append("")
    lines.append("</details>")
    return "\n".join(lines)


def _links(context):
    links = []
    if context.get("pages_url"):
        links.append("[Open the report](%s)" % context["pages_url"])
    if context.get("artifact_url"):
        links.append("[Download the artifact](%s)" % context["artifact_url"])
    if context.get("run_url"):
        links.append("[Workflow run](%s)" % context["run_url"])

    if not links:
        return ""

    return "· ".join(link + " " for link in links).strip()


# -- small text helpers -----------------------------------------------------

def _plural(count, word):
    return word if count == 1 else word + "s"


def _trim(number):
    text = "%.2f" % float(number)
    return text.rstrip("0").rstrip(".") or "0"


def _seconds(value):
    value = _float(value)
    if value < 60:
        return "%ss" % _trim(value)

    minutes, seconds = divmod(int(round(value)), 60)
    if minutes < 60:
        return "%dm %02ds" % (minutes, seconds)

    hours, minutes = divmod(minutes, 60)
    return "%dh %02dm" % (hours, minutes)


def _escape(text):
    return text.replace("<", "&lt;").replace(">", "&gt;")


def _cell(text):
    """A name, safe to drop into a markdown table cell."""
    flat = str(text).replace("|", "\\|").replace("\n", " ").replace("\r", " ")
    return _code(flat)


def _defang(text):
    """Stop a line of somebody else's text reading as a workflow command.

    Test names and assertion messages come out of the tests, and on a
    fork's pull request the tests are the fork author's. A line opening
    with :: is how a workflow command is written, so the opening is broken
    here rather than trusted not to appear.
    """
    lines = []
    for line in str(text).splitlines():
        if line.lstrip().startswith("::"):
            line = line.replace("::", ":\u200b:", 1)
        lines.append(line)

    return "\n".join(lines)


def _code(text):
    """Inline code that survives a backtick in the name."""
    text = str(text)
    fence = "`" * (_longest_run(text, "`") + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return "%s%s%s%s%s" % (fence, pad, text, pad, fence)


def _fence_safe(text, limit=1200):
    """Trim a captured message and stop it closing the fence it sits in."""
    text = _defang(text)
    if len(text) > limit:
        text = text[:limit] + "\n... trimmed, the full message is in the report."

    return text.replace("```", "'''")


def _longest_run(text, char):
    best = run = 0
    for letter in text:
        run = run + 1 if letter == char else 0
        best = max(best, run)

    return best


# ---------------------------------------------------------------------------
# thresholds
# ---------------------------------------------------------------------------

def gate(run, exit_code, options):
    """(ok, reasons) - whether the job should go red, and why."""
    reasons = []

    if exit_code == 5:
        if _flag(options.fail_on_empty):
            reasons.append("pytest collected no tests. If a run that collects "
                           "nothing is expected here, set fail-on-empty: 'false'.")
    elif _flag(options.fail_on_error) and exit_code != 0:
        reasons.append("pytest exited with code %s - %s"
                       % (exit_code, EXIT_CODES.get(exit_code, "see the pytest log above")))

    if _flag(options.fail_on_error) and exit_code == 0 and not run.found:
        reasons.append(
            "pytest exited cleanly but wrote no %s to %s - install "
            "pytest-html-reporter into the environment that runs the tests, or "
            "point report-path at where it does write" % (JSON_NAME, run.json_path))

    minimum = _optional_float(options.min_pass_rate, "min-pass-rate")
    if minimum is not None:
        rate = run.pass_rate
        if rate is None:
            reasons.append("min-pass-rate is set to %s%% but no test produced a "
                           "pass or a failure to measure" % _trim(minimum))
        elif rate < minimum:
            reasons.append("pass rate %s%% is below the required %s%%"
                           % (_trim(rate), _trim(minimum)))

    minimum = _optional_float(options.min_coverage, "min-coverage")
    if minimum is not None:
        coverage = run.coverage
        if not coverage:
            reasons.append("min-coverage is set to %s%% but this run produced no "
                           "coverage data" % _trim(minimum))
        elif _float(coverage.get("percent")) < minimum:
            reasons.append("coverage %s%% is below the required %s%%"
                           % (_trim(_float(coverage.get("percent"))), _trim(minimum)))

    return (not reasons), reasons


def _flag(value):
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _optional_float(value, name):
    value = str(value or "").strip()
    if not value:
        return None

    try:
        return float(value)
    except ValueError:
        warn("%s is not a number: %r - the threshold is ignored." % (name, value))
        return None


# ---------------------------------------------------------------------------
# building the pytest command
# ---------------------------------------------------------------------------

# input name -> pytest flag, for the options that take a single value.
SINGLE = (
    ("title", "--title"),
    ("environment", "--environment"),
    ("archive_count", "--archive-count"),
    ("archive_days", "--archive-days"),
    ("archive_since", "--archive-since"),
    ("report_logs", "--report-logs"),
    ("report_log_limit", "--report-log-limit"),
    ("report_attachments", "--report-attachments"),
    ("report_attachment_limit", "--report-attachment-limit"),
    ("report_steps", "--report-steps"),
    ("report_step_limit", "--report-step-limit"),
    ("report_coverage", "--report-coverage"),
    ("report_coverage_file", "--report-coverage-file"),
    ("report_coverage_limit", "--report-coverage-limit"),
)

# input name -> pytest flag, for the options given one per line and repeated.
REPEATED = (
    ("build_info", "--build-info"),
    ("report_links", "--report-link"),
)


def supported(help_text):
    """The long options this pytest understands, or None when unknown.

    A user is free to pin their own version of the plugin, and the older ones
    have fewer flags. `pytest --help` lists what is really there, so the
    answer comes from the installed plugin rather than from this file's idea
    of it. None means the probe failed, in which case nothing is filtered -
    degrading to the previous behaviour beats dropping every flag.
    """
    if not help_text:
        return None

    return set(re.findall(r"--[a-z0-9][a-z0-9-]*", help_text))


def build_args(env, report_path, help_text=None):
    """The pytest argument list, from the action's inputs in `env`."""
    known = supported(help_text)
    dropped = []

    def take(flag):
        if known is None or flag in known:
            return True

        dropped.append(flag)
        return False

    args = ["--html-report=%s" % report_path]

    for name, flag in SINGLE:
        value = str(env.get("PHR_" + name.upper(), "")).strip()
        if value and take(flag):
            args.append("%s=%s" % (flag, value))

    for name, flag in REPEATED:
        lines = [line.strip() for line
                 in str(env.get("PHR_" + name.upper(), "")).splitlines()]
        lines = [line for line in lines if line and not line.startswith("#")]
        if lines and take(flag):
            args.extend("%s=%s" % (flag, line) for line in lines)

    # Forced off unless the caller asks otherwise. "auto" is not a defence
    # the action controls: the CLI beats the ini, so a repo carrying
    # `report_open = always` in its pytest.ini opens a browser with no TTY, CI
    # or DISPLAY check at all - which on a runner means handing the report to
    # a console browser and waiting for it.
    if take("--report-open"):
        args.append("--report-open=%s"
                    % (str(env.get("PHR_REPORT_OPEN", "")).strip() or "none"))

    for line in str(env.get("PHR_TESTS", "")).splitlines():
        line = line.strip()
        if line:
            args.append(line)

    extra = str(env.get("PHR_PYTEST_ARGS", "")).strip()
    if extra:
        import shlex

        args.extend(shlex.split(extra))

    for flag in dropped:
        warn("the installed pytest-html-reporter has no %s, so that input was "
             "left out of the run rather than failing it. Upgrade the plugin - "
             "or drop the input - to use it." % flag)

    return args


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_resolve(options):
    """Pin down where this run's report goes, before pytest is told about it."""
    raw = normalise(options.path.strip() or "report")
    expanded = expand_time(raw)
    report_dir, report_name = resolve_report(expanded)

    head = expanded.rsplit("/", 1)[0]
    if ".html" in expanded and head != expanded and ".html" in head:
        warn("report-path %r names a folder that itself contains '.html'. The "
             "plugin reads that as 'no folder', so the report lands in %s. Rename "
             "the folder to put it where you meant." % (expanded, report_dir))

    report_file = os.path.join(report_dir, report_name)
    json_path = os.path.join(report_dir, JSON_NAME)

    os.makedirs(report_dir, exist_ok=True)

    write_output("report-dir", report_dir)
    write_output("report-name", report_name)
    write_output("report-file", report_file)
    write_output("json-path", json_path)
    write_output("archive-dir", os.path.join(report_dir, ARCHIVE_DIR))
    write_output("screenshot-dir", os.path.join(report_dir, SCREENSHOT_DIR))
    # What pytest is handed: the expanded value, so a %H in the path cannot
    # expand twice and leave the action looking in the wrong folder.
    write_output("html-report", expanded)

    if expanded != raw:
        notice("report-path %s expanded to %s" % (raw, expanded))

        if _flag(options.history):
            warn("report-path %s expands to a new folder each run, so every "
                 "build starts with an empty history and the Trends, Archives "
                 "and Analytics tabs will stay empty. Use a fixed report-path "
                 "with history, and put the date in the artifact name instead."
                 % raw)

    return 0


def cmd_prime(options):
    """Get a restored history into a state the plugin will actually extend.

    Two things have to be true for a build to join the archive, and a cache
    restore satisfies neither on its own.
    """
    directory = options.report_dir
    archive = os.path.join(directory, ARCHIVE_DIR)

    # 1. A build is archived by rotating the *previous* output.json into
    #    archive/, and archive_data() only does that when the previous report
    #    file is on disk. On a fresh runner it never is, so every run would
    #    quietly replace its predecessor and the archive would stay empty.
    #    An empty placeholder is enough - the run overwrites it.
    report = os.path.join(directory, options.report_name)
    if os.path.isfile(os.path.join(directory, JSON_NAME)) and not os.path.isfile(report):
        os.makedirs(directory, exist_ok=True)
        with open(report, "w", encoding="utf-8") as handle:
            handle.write("")

        notice("stood in an empty %s so this build's predecessor joins the "
               "archive" % options.report_name)

    # 2. The plugin reads every archive/*.json without guarding the read, so
    #    one truncated or foreign file raises inside pytest_terminal_summary
    #    and no report is written at all. A cache is exactly where such a file
    #    comes from, so the bad ones are moved aside here.
    quarantined = 0
    for name in sorted(os.listdir(archive) if os.path.isdir(archive) else []):
        if not name.endswith(".json"):
            continue

        path = os.path.join(archive, name)
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)

            if str((data or {}).get("status", "")).upper() not in ("PASS", "FAIL"):
                raise ValueError("no usable status")
        except Exception:
            os.rename(path, path + ".unreadable")
            quarantined += 1

    if quarantined:
        warn("%s archived %s could not be read and %s set aside. The plugin "
             "reads the archive without guarding it, and one bad file stops the "
             "whole report being written."
             % (quarantined, _plural(quarantined, "build"),
                "was" if quarantined == 1 else "were"))

    # Stale screenshots outlive their run: the plugin's own cleanup appends
    # /pytest_screenshots to the raw --html-report value, which misses
    # entirely when that value named a file rather than a folder.
    screenshots = os.path.join(directory, SCREENSHOT_DIR)
    if os.path.isdir(screenshots):
        import shutil

        shutil.rmtree(screenshots, ignore_errors=True)

    return 0


def cmd_args(options):
    """Write the pytest arguments, NUL-separated, for bash to read back."""
    help_text = ""
    if options.help_text:
        try:
            with open(options.help_text, encoding="utf-8", errors="replace") as handle:
                help_text = handle.read()
        except (IOError, OSError):
            help_text = ""

    args = build_args(os.environ, options.html_report, help_text)

    with open(options.out, "wb") as handle:
        for arg in args:
            handle.write(arg.encode("utf-8") + b"\0")

    sys.stdout.write("pytest %s\n" % " ".join(_quote(arg) for arg in args))
    return 0


def _quote(arg):
    import shlex

    return shlex.quote(arg)


USAGE_ERROR = re.compile(
    r"^(ERROR: --|pytest: error:|.*unrecognized arguments)", re.MULTILINE)


def usage_errors(path):
    """The lines of a pytest log that explain a usage error."""
    if not path:
        return []

    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except (IOError, OSError):
        return []

    return [line.strip() for line in text.splitlines()
            if USAGE_ERROR.match(line.strip())][:5]


def cmd_summarize(options):
    """Turn output.json into outputs, a job summary and a comment body."""
    run = Run.load(options.json)
    counts = run.counts

    wall = _optional_float(options.wall_clock, "wall-clock")
    context = {
        "title": options.title,
        "report_dir": os.path.dirname(options.json),
        "wall_clock": wall,
        "failure_limit": options.failure_limit,
        "suite_limit": options.suite_limit,
        "slowest_limit": options.slowest_limit,
        "artifact_url": options.artifact_url,
        "pages_url": options.pages_url,
        "run_url": options.run_url,
    }

    markdown = render(run, context)

    for key, label, _ in STATUS_LABELS:
        write_output({"pass": "passed", "fail": "failed", "error": "errors",
                      "skip": "skipped", "xpass": "xpassed", "xfail": "xfailed",
                      "rerun": "rerun"}[key], counts[key])

    write_output("total", run.total)
    write_output("suites", run.suites)
    write_output("status", run.status)
    write_output("pass-rate", "" if run.pass_rate is None else _trim(run.pass_rate))
    write_output("tests-duration", run.duration)
    write_output("wall-clock", "" if wall is None else _trim(wall))
    write_output("coverage", "" if not run.coverage
                 else _trim(_float(run.coverage.get("percent"))))
    write_output("report-found", "true" if run.found else "false")
    write_output("summary", markdown)

    if options.coverage_file and not run.coverage:
        warn("report-coverage-file was set to %r, and no coverage reached the "
             "report. The plugin treats that input as final - it does not fall "
             "back to the coverage this run measured - so check the file exists "
             "by the time the tests finish and holds a coverage.json, a Cobertura "
             "coverage.xml or a .coverage data file. Leaving the input empty lets "
             "the plugin find the coverage itself." % options.coverage_file)

    if _flag(options.job_summary):
        if not write_summary(markdown):
            sys.stdout.write(markdown)

    if options.comment_body:
        with open(options.comment_body, "w", encoding="utf-8") as handle:
            handle.write(COMMENT_MARKER + "\n"
                         + trim(markdown, COMMENT_LIMIT, "a pull request comment"))

    exit_code = _int(options.exit_code)

    # pytest refused an argument. The message it printed names a *flag*, and
    # the reader typed an input - so hand them the message rather than making
    # them go and find it in the log.
    if exit_code == 4:
        for line in usage_errors(options.pytest_log):
            fail("%s  (this action's inputs become those flags, and so do the "
                 "matching keys in your pytest.ini)" % line)

    # The plugin calls a run FAIL only when a suite holds a failure or an
    # error. pytest has more ways to exit non-zero than that, so when the two
    # disagree the exit code is the one that decides, and the difference is
    # worth saying out loud.
    if run.found and exit_code not in (0, 1) and run.status == "PASS":
        warn("every test that ran passed, but pytest exited with %s. The job "
             "is decided on the exit code; the report shows the tests."
             % exit_code)

    ok, reasons = gate(run, exit_code, options)
    write_output("gate-passed", "true" if ok else "false")
    write_output("gate-reasons", "; ".join(reasons))

    for reason in reasons:
        fail(reason)

    if not ok:
        write_summary("\n> [!CAUTION]\n> " + "\n> ".join(reasons) + "\n")

    return 0 if ok else 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog="phr", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    resolve = sub.add_parser("resolve", help="work out where the report lands")
    resolve.add_argument("--path", default="report")
    resolve.add_argument("--history", default="false")
    resolve.set_defaults(handler=cmd_resolve)

    prime = sub.add_parser("prime", help="ready a restored history for this run")
    prime.add_argument("--report-dir", required=True)
    prime.add_argument("--report-name", default=DEFAULT_REPORT_NAME)
    prime.set_defaults(handler=cmd_prime)

    args = sub.add_parser("args", help="build the pytest argument list")
    args.add_argument("--html-report", required=True)
    args.add_argument("--out", required=True)
    args.add_argument("--help-text", default="",
                      help="a file holding `pytest --help`, so that only flags "
                           "the installed plugin has are passed")
    args.set_defaults(handler=cmd_args)

    summarize = sub.add_parser("summarize", help="read output.json and report on it")
    summarize.add_argument("--json", required=True)
    summarize.add_argument("--title", default="pytest-html-reporter")
    summarize.add_argument("--exit-code", default="0")
    summarize.add_argument("--wall-clock", default="")
    summarize.add_argument("--failure-limit", type=int, default=10)
    summarize.add_argument("--suite-limit", type=int, default=20)
    summarize.add_argument("--slowest-limit", type=int, default=5)
    summarize.add_argument("--job-summary", default="true")
    summarize.add_argument("--comment-body", default="")
    summarize.add_argument("--artifact-url", default="")
    summarize.add_argument("--pages-url", default="")
    summarize.add_argument("--run-url", default="")
    summarize.add_argument("--fail-on-error", default="true")
    summarize.add_argument("--min-pass-rate", default="")
    summarize.add_argument("--min-coverage", default="")
    summarize.add_argument("--coverage-file", default="")
    summarize.add_argument("--pytest-log", default="")
    summarize.add_argument("--fail-on-empty", default="true")
    summarize.set_defaults(handler=cmd_summarize)

    options = parser.parse_args(argv)
    if not getattr(options, "handler", None):
        parser.print_help()
        return 2

    return options.handler(options)


if __name__ == "__main__":
    sys.exit(main())
