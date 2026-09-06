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


class Unusable(Exception):
    """An input this action cannot act on. Reported, not traced."""

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

# The two statuses that make a run red. An xFAIL is a failure that was asked
# for and a SKIP is a test that never ran, so neither is one.
FAILING = ("FAIL", "ERROR")

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


def reescape(path):
    """`path` written so the plugin's own expansion returns it unchanged.

    The action expands the placeholders once, up front, so that it and the
    plugin cannot disagree about where the report went. But the plugin
    expands whatever it is given, and a percent that survived the first pass
    - the one in "100%%pass" - would be read as a directive on the second.
    Doubling every remaining percent makes that second pass give this string
    back, because %% is how the plugin spells a literal one.
    """
    return path.replace("%", "%%")


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

    cut = markdown.encode("utf-8")[:room].decode("utf-8", "ignore")

    # Cutting mid-block would leave a fence or a <details> that nothing
    # closes, and everything after it folded away. Close what is open.
    if cut.count("```") % 2:
        cut += "\n```"

    cut += "\n</details>" * (cut.count("<details>") - cut.count("</details>"))

    return cut + tail


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
    def load(cls, json_path, previous=None):
        """The build at `json_path`, unless it is the one `previous` names.

        With history on, a restored cache puts the last build's output.json
        in place before pytest runs. If this run then writes none of its own
        - it crashed, it collected nothing - what is left on disk is the
        previous build, and summarising that would report somebody else's
        passes as this run's.
        """
        run = cls._read(json_path)

        if run.found and previous and str(run.data.get("start_time")) == str(previous):
            warn("the report at %s is the one restored from the build history, "
                 "not one this run wrote. Reporting it as this run's result "
                 "would be a lie, so it is being read as no report at all."
                 % json_path)
            return cls(None, json_path)

        return run

    @classmethod
    def _read(cls, json_path):
        try:
            with open(json_path, encoding="utf-8") as handle:
                data = json.load(handle)

            if not isinstance(data, dict):
                warn("%s holds %s where the report should be, so it is being "
                     "read as no report at all."
                     % (json_path, type(data).__name__))
                return cls(None, json_path)

            return cls(data, json_path)
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

        return 100.0 * counts["pass"] / decisive

    @property
    def coverage(self):
        block = self.data.get("coverage")
        return block if isinstance(block, dict) else None

    @property
    def duration(self):
        """Summed test durations. Wall clock is measured by the action itself."""
        return round(sum(test["duration"] for test in self.tests()), 2)

    # -- detail -----------------------------------------------------------

    def _suite_items(self):
        """(key, suite) pairs, in report order.

        The keys are the report's own - stringified indices - and they are
        carried rather than re-numbered because half of a row's id in the
        HTML is one of them. Re-numbering here would line a summary up
        against the wrong anchor the moment a suite arrived out of order.
        """
        suites = ((self.data.get("content") or {}).get("suites")) or {}

        if isinstance(suites, dict):
            # The keys are stringified indices; sort numerically so the
            # report and the summary list the suites in the same order.
            return [(str(key), suites[key]) for key in sorted(suites, key=_sort_key)
                    if isinstance(suites[key], dict)]

        return [(str(index), suite) for index, suite in enumerate(suites)
                if isinstance(suite, dict)]

    def _suites(self):
        return [suite for _, suite in self._suite_items()]

    def tests(self):
        """Every test in the run, in report order, with its place in it.

        ``row`` is the id the report's own table gives the row - the suite's
        key and the test's, joined the way the plugin joins them - so that a
        test here can be matched to the anchor the HTML carries for it.

        ``identity`` is what one test is called across two builds, so that a
        comparison can line this run up against another. Two tests in one
        file can share a name - one in a class, one beside it - and a
        repeat is numbered rather than left to collide, which would have a
        comparison call one of them fixed and the other new every time.
        """
        out = []
        seen = {}

        for suite_key, suite in self._suite_items():
            name = str(suite.get("suite_name") or "unnamed")

            for test_key, test in _test_items(suite):
                test_name = str(test.get("test_name") or "unnamed")
                identity = "%s::%s" % (name, test_name)
                repeat = seen.get(identity, 0) + 1
                seen[identity] = repeat

                out.append({
                    "suite": name,
                    "test": test_name,
                    "status": str(test.get("status") or "").upper(),
                    "message": str(test.get("message") or "").strip(),
                    "rerun": _int(test.get("rerun")),
                    "duration": _float(test.get("duration")),
                    "row": "%s-%s" % (suite_key, test_key),
                    "identity": identity if repeat == 1 else "%s#%d" % (identity, repeat),
                })

        return out

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
        return [test for test in self.tests() if test["status"] in FAILING]

    def flaky(self):
        """Tests that failed, were retried, and then did not fail.

        A rerun only happens after a failure, so a test carrying one that
        ends the run green gave two answers to the same question. It is
        counted apart from the failures because it is a different problem:
        a failing test blocks the merge, a flaky one wastes everybody's
        afternoon and blocks nothing until it is looked at.
        """
        return [test for test in self.tests()
                if test["rerun"] and test["status"] not in FAILING]

    def slowest(self, limit=5):
        # A skipped test's duration is the cost of deciding to skip it,
        # which is nobody's idea of a slow test.
        tests = [test for test in self.tests() if test["status"] != "SKIP"]
        tests.sort(key=lambda item: item["duration"], reverse=True)

        return [test for test in tests[:limit] if test["duration"] > 0]


def _test_items(suite):
    """(key, test) pairs for a suite, in report order.

    The plugin writes a dict keyed by a stringified index. Anything that has
    been through a tool of its own may hand back a list instead, and one
    reader coping with that while another raises is worse than either.
    """
    tests = (suite or {}).get("tests") or {}

    if isinstance(tests, dict):
        return [(str(key), tests[key]) for key in sorted(tests, key=_sort_key)
                if isinstance(tests[key], dict)]

    return [(str(index), test) for index, test in enumerate(tests)
            if isinstance(test, dict)]


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
# linking one failure to its row in the report
# ---------------------------------------------------------------------------

# A row id the plugin hands out: "test-", a slug of the node id, and six hex
# digits of its digest. Matched rather than trusted, because the value goes
# into an href in a comment posted under the repository's own identity.
_ANCHOR = re.compile(r"^test-[a-z0-9-]+$")

# The row's own cell says which suite and which test it is, and those two
# numbers are the keys output.json files the same test under. They are what
# ties an anchor to a run this module has read, without the summary having to
# rely on the two documents listing their rows in the same order.
_ROW_TARGET = re.compile(r'data-jump="test"\s+data-target="(\d+-\d+)"')


def anchors(report_file):
    """{row id: anchor} read back out of the report the plugin just wrote.

    The report gives every row an id built from the test's *node id*, which
    is what a `#` link to one failure has to carry. The node id is not in
    output.json - only the suite and the test name are, and a test inside a
    class is listed under a name its node id does not hold - so the anchors
    are read out of the HTML rather than derived a second time here.

    Deriving them would put a link in the summary that quietly resolves to
    nothing for exactly the tests that are hardest to find by hand. An empty
    map is the honest answer when the report cannot be read, and it costs
    the reader a link rather than sending them somewhere wrong.
    """
    try:
        with open(report_file, encoding="utf-8", errors="replace") as handle:
            html = handle.read()
    except (IOError, OSError):
        return {}

    found = {}

    # Split rather than matched across the whole document: the report is a
    # megabyte and a half of one line, and a pattern spanning two rows would
    # happily pair one row's id with the next row's cell.
    for chunk in html.split('<tr id="test-')[1:]:
        end = chunk.find('"')
        if end < 0:
            continue

        anchor = "test-" + chunk[:end]
        if not _ANCHOR.match(anchor):
            continue

        # Cut at the next row so that a row without a cell of its own - a
        # report shaped differently by some later version - cannot quietly
        # borrow the next row's and put the link on the wrong test.
        target = _ROW_TARGET.search(chunk.split("<tr", 1)[0])
        if target:
            found.setdefault(target.group(1), anchor)

    return found


def link_rows(tests, report_url, found):
    """Give each test the URL of its own row, when there is one to give."""
    for test in tests:
        anchor = found.get(test["row"])
        test["anchor"] = anchor or ""
        test["url"] = "%s#%s" % (report_url, anchor) if anchor and report_url else ""

    return tests


# ---------------------------------------------------------------------------
# comparing this run with another
# ---------------------------------------------------------------------------

def compare(run, baseline):
    """What this run changed, measured against `baseline`.

    Tests are lined up by identity - the suite and the name - rather than by
    position, so a test added at the top of a file does not read as every
    test below it having changed.
    """
    now, then = run.tests(), baseline.tests()
    before = dict((test["identity"], test) for test in then)
    after = dict((test["identity"], test) for test in now)

    new_failures, fixed, still_failing = [], [], []

    for test in now:
        was = before.get(test["identity"])

        if test["status"] in FAILING:
            if was is None:
                new_failures.append(dict(test, was=""))
            elif was["status"] in FAILING:
                still_failing.append(test)
            else:
                new_failures.append(dict(test, was=was["status"]))
        elif was is not None and was["status"] in FAILING:
            fixed.append(test)

    return {
        "new_failures": new_failures,
        "fixed": fixed,
        "still_failing": still_failing,
        "added": [test for test in now if test["identity"] not in before],
        "removed": [test for test in then if test["identity"] not in after],
        "pass_rate": _delta(run.pass_rate, baseline.pass_rate),
        "coverage": _delta(_percent(run.coverage), _percent(baseline.coverage)),
        "total": _delta(run.total, baseline.total),
        "failed": _delta(run.counts["fail"] + run.counts["error"],
                         baseline.counts["fail"] + baseline.counts["error"]),
    }


def _percent(coverage):
    return None if not coverage else _float(coverage.get("percent"))


def _delta(now, was):
    """(now, was, difference) - with None wherever a side has no number.

    A run that measured no coverage and a baseline that did are not a drop
    of everything; they are two things that cannot be subtracted, and the
    difference is left unsaid rather than invented.
    """
    if now is None or was is None:
        return (now, was, None)

    return (now, was, now - was)


def load_baseline(json_path, zip_path):
    """The build to compare against, from a file or from an artifact zip.

    A path wins over a zip: the first is what somebody asked for by hand,
    the second is what the action found on its own.
    """
    if json_path:
        run = Run.load(json_path)
        if not run.found:
            warn("baseline-json %r could not be read, so this run is reported "
                 "on its own." % json_path)
        return run

    if zip_path:
        return _baseline_from_zip(zip_path)

    return Run(None, "")


def _baseline_from_zip(zip_path):
    """The output.json inside a report artifact downloaded for comparison."""
    import zipfile

    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = [name for name in archive.namelist()
                     if name.rsplit("/", 1)[-1] == JSON_NAME]

            # The artifact holds the run's own output.json and, when history
            # is on, one per archived build beside it. The current one is the
            # shallowest that is not inside archive/.
            current = [name for name in names
                       if ("/%s/" % ARCHIVE_DIR) not in "/" + name]
            if not current:
                warn("the baseline artifact holds no %s outside its %s folder, "
                     "so there is nothing to compare against."
                     % (JSON_NAME, ARCHIVE_DIR))
                return Run(None, zip_path)

            chosen = sorted(current, key=lambda name: (name.count("/"), name))[0]
            data = json.loads(archive.read(chosen).decode("utf-8"))

            if not isinstance(data, dict):
                raise ValueError("%s holds %s" % (chosen, type(data).__name__))

            return Run(data, chosen)
    except Exception as error:
        warn("the baseline artifact at %s could not be read (%s), so this run "
             "is reported on its own." % (zip_path, error))
        return Run(None, zip_path)


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

    comparison = context.get("comparison")
    if comparison:
        lines.append(_comparison(comparison, context))
        lines.append("")

    failures = _linked(run.failures(), context)
    if failures:
        lines.append(_failures(failures, _limit(context.get("failure_limit"), 10),
                               comparison))
        lines.append("")

    flaky = _linked(run.flaky(), context)
    if flaky:
        lines.append(_flaky(flaky, _limit(context.get("failure_limit"), 10)))
        lines.append("")

    rows = run.suite_rows()
    if len(rows) > 1:
        lines.append(_suites_table(rows, _limit(context.get("suite_limit"), 20)))
        lines.append("")

    slowest = run.slowest(_limit(context.get("slowest_limit"), 5))
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
        parts.append("pass rate %s%%" % _trim(round(rate, 2)))

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


def _linked(tests, context):
    """Each test, carrying the URL of its own row in the published report."""
    return link_rows(tests, context.get("pages_url") or "",
                     context.get("anchors") or {})


def _row_link(test):
    """A test's name, as a link to its row when the report has an address.

    The anchor is matched against the plugin's own shape before it gets
    here, and the report URL is the repository's own input, so what goes
    into the href is never the test's to write.
    """
    label = _summary(test["test"])
    url = test.get("url")

    return '<a href="%s">%s</a>' % (_href(url), label) if url else label


def _href(url):
    """A URL, safe as an attribute value."""
    return (str(url).replace("&", "&amp;").replace('"', "&quot;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def _failures(failures, limit, comparison=None):
    lines = ["### Failures"]
    fresh = set(test["identity"]
                for test in (comparison or {}).get("new_failures", []))

    for failure in failures[:limit]:
        heading = "%s › %s" % (_summary(failure["suite"]), _row_link(failure))
        if failure["status"] == "ERROR":
            heading = "\U0001f6a8 " + heading
        if failure["identity"] in fresh:
            heading += " · **new**"
        if failure["rerun"]:
            heading += " (rerun %s×)" % failure["rerun"]

        lines.append("")
        lines.append("<details><summary>%s</summary>" % heading)
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


def _flaky(tests, limit):
    """The tests that only passed because they were run again.

    Listed apart from the failures because they are a different problem: the
    run is green, nothing is blocked, and without a section of its own that
    is the last anybody hears of it until it fails for real.
    """
    lines = ["### Flaky", "",
             "%s %s passed only after a retry." % (len(tests), _plural(len(tests), "test")),
             "",
             "| Test | Retries |", "| --- | ---: |"]

    for test in tests[:limit]:
        lines.append("| %s › %s | %s |" % (
            _cell(test["suite"]), _row_link(test), test["rerun"]))

    dropped = len(tests) - limit
    if dropped > 0:
        lines.append("")
        lines.append("_%s further %s not listed here._"
                     % (dropped, _plural(dropped, "test")))

    return "\n".join(lines)


def _comparison(comparison, context):
    """This run set beside the build it is being compared with."""
    label = context.get("comparison_label") or "the baseline"
    url = context.get("comparison_url") or ""
    heading = "### Compared with %s" % (
        "[%s](%s)" % (label, url) if url else label)

    lines = [heading, "",
             "| | This run | Baseline | Δ |",
             "| --- | ---: | ---: | ---: |"]

    for name, key, suffix in (("Pass rate", "pass_rate", "%"),
                              ("Coverage", "coverage", "%"),
                              ("Failed", "failed", ""),
                              ("Tests", "total", "")):
        now, was, difference = comparison[key]
        if now is None and was is None:
            continue

        lines.append("| %s | %s | %s | %s |" % (
            name, _measure(now, suffix), _measure(was, suffix),
            _difference(difference, suffix)))

    verdict = []
    for count, word in ((len(comparison["new_failures"]), "new failure"),
                        (len(comparison["fixed"]), "fixed"),
                        (len(comparison["still_failing"]), "still failing")):
        if count:
            verdict.append("**%s** %s" % (count, _plural(count, word)
                                          if word == "new failure" else word))

    lines.append("")
    lines.append((", ".join(verdict) + ".") if verdict
                 else "No test changed which side of the line it is on.")

    for title, tests in (("New failures", comparison["new_failures"]),
                         ("Fixed", comparison["fixed"])):
        listed = _linked(tests, context)[:_limit(context.get("failure_limit"), 10)]
        if not listed:
            continue

        lines.append("")
        lines.append("**%s**" % title)
        lines.append("")
        for test in listed:
            lines.append("- %s › %s" % (_summary(test["suite"]), _row_link(test)))

        dropped = len(tests) - len(listed)
        if dropped > 0:
            lines.append("- _and %s more_" % dropped)

    return "\n".join(lines)


def _measure(value, suffix):
    return "–" if value is None else "%s%s" % (_trim(round(value, 2)), suffix)


def _difference(value, suffix):
    """A delta, signed, with a dash where the two sides cannot be subtracted.

    A typographic minus rather than a hyphen, because this one is read: it
    sets beside the plus, which is a full-width glyph, and a hyphen next to
    one in a right-aligned column reads as a dash. `_signed` writes the
    output a workflow parses, and that one keeps the ASCII sign.
    """
    if value is None:
        return "–"

    rounded = round(value, 2)
    if rounded == 0:
        return "±0"

    return "%s%s%s" % ("+" if rounded > 0 else "−",
                       _trim(abs(rounded)), suffix)


def _suites_table(rows, limit):
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

def _limit(value, fallback):
    """How many to list. 0 lists none; an unset input takes the default."""
    if value is None or str(value).strip() == "":
        return fallback

    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return fallback


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
    return str(text).replace("<", "&lt;").replace(">", "&gt;")


def _name(text, in_table=False):
    """A test or suite name, safe wherever this summary puts one.

    Everything here is written by the tests, and on a fork's pull request the
    tests are the fork author's, so a name gets three escapes:

    HTML, because the failures sit inside a <details> block and a name of
    "</details>" would close it and take the rest of the summary with it.

    Markdown, because "[Security notice](http://evil)" must not become a link
    in a comment posted under the repository's own identity. An <code>
    element with the angle brackets already gone is the one rendering of a
    name that is only ever the name.

    And the pipe, in a table, because GFM ends a cell at an unescaped one
    however deeply it is nested.
    """
    escaped = _defang(str(text)).replace("&", "&amp;")
    escaped = escaped.replace("<", "&lt;").replace(">", "&gt;")
    escaped = escaped.replace("[", "&#91;").replace("]", "&#93;")
    escaped = escaped.replace("\n", " ").replace("\r", " ")

    if in_table:
        escaped = escaped.replace("|", "&#124;")

    return "<code>%s</code>" % escaped


def _cell(text):
    """A name, safe inside a markdown table cell."""
    return _name(text, in_table=True)


def _summary(text):
    """A name, safe inside a <summary> tag."""
    return _name(text)


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
    """Trim a captured message and stop it closing anything it sits in.

    A fence, and the <details> block around it. The fence content should be
    literal text to any markdown renderer, but a failure that silently
    collapses the rest of the summary is a poor trade against a zero-width
    space in the middle of a traceback nobody was reading closely.
    """
    text = _defang(text).replace("</details", "</\u200bdetails")
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
# annotations
# ---------------------------------------------------------------------------

# The tail of a pytest traceback: "tests/test_cart.py:15: RuntimeError". An
# ERROR carries the whole traceback, so this finds the frame that raised. A
# FAIL carries only the lines pytest prefixed with "E   ", which is why the
# line number for one is looked up in the source instead.
_FRAME = re.compile(r"^(?P<path>[^\s][^:]*\.py):(?P<line>\d+):", re.MULTILINE)

# GitHub caps what one step may annotate. Ten of each kind is what it shows,
# and an eleventh is dropped without a word, so the summary - which has no
# such cap - is where the rest are.
ANNOTATION_LIMIT = 10

# An annotation is shown in a hover card and in the run's annotation list,
# and neither is a place to read a long traceback. The report has all of it.
ANNOTATION_MESSAGE_LIMIT = 900


def annotate(run, comparison, options, roots):
    """One annotation per failure, placed on the line the test is written at.

    Errors for what failed, warnings for what was retried into a pass. New
    failures are annotated first, because a limit that drops something
    should drop the failure the base branch already had rather than the one
    this change introduced.
    """
    limit = _limit(options.annotation_limit, ANNOTATION_LIMIT)
    if limit <= 0:
        return 0

    new = set(test["identity"] for test in (comparison or {}).get("new_failures", []))

    failures = sorted(run.failures(),
                      key=lambda test: 0 if test["identity"] in new else 1)

    written = 0
    for test in failures[:limit]:
        path, line = locate(test, roots)
        label = "errored" if test["status"] == "ERROR" else "failed"
        if test["identity"] in new:
            label = "newly " + label

        _annotation("error", test, "%s %s" % (test["test"], label), path, line)
        written += 1

    for test in run.flaky()[:limit]:
        path, line = locate(test, roots)
        _annotation("warning", test,
                    "%s passed on retry %s×" % (test["test"], test["rerun"]),
                    path, line,
                    "Failed, was retried %s %s, and passed. The run is green "
                    "and this test gave two answers to the same question in "
                    "it." % (test["rerun"], _plural(test["rerun"], "time")))
        written += 1

    return written


def _annotation(kind, test, title, path, line, message=None):
    """One workflow command, with the parts GitHub reads escaped as it asks.

    The message is somebody else's - a test wrote it, and on a fork's pull
    request the tests are the fork author's - so every newline in it is
    escaped, which leaves it as one line that cannot begin a command of its
    own however it starts.
    """
    properties = [("title", "pytest-html-reporter: " + title)]
    if path:
        properties.append(("file", path))
        if line:
            properties.extend([("line", str(line)), ("col", "1")])

    body = "%s\n\n%s" % (test["identity"],
                         message or test["message"] or "No message was captured.")

    sys.stdout.write("::%s %s::%s\n" % (
        kind,
        ",".join("%s=%s" % (name, _property(value)) for name, value in properties),
        _oneline(_defang(body[:ANNOTATION_MESSAGE_LIMIT]))))


def _property(value):
    """A workflow command property, escaped the way GitHub reads them back."""
    return (_oneline(value)
            .replace(":", "%3A")
            .replace(",", "%2C"))


def locate(test, roots):
    """(path, line) for a test, as a path GitHub can hang an annotation on.

    The path has to be relative to the repository, because that is what a
    diff is addressed by. A test whose file cannot be found under any root -
    a suite name that is a node id from a plugin, a run whose rootdir is
    outside the checkout - gets no path at all, and the annotation still
    reaches the run's annotation list without pointing at the wrong file.

    The last root is the checkout, by the way `_roots` builds them, and it is
    what the path comes out relative to.
    """
    absolute = _source_file(test["suite"], roots)
    if not absolute:
        return "", None

    workspace = roots[-1]
    try:
        path = os.path.relpath(absolute, workspace)
    except ValueError:
        # Windows, and the checkout is on another drive from the run.
        return "", None

    if path.startswith(".."):
        # Outside the checkout, so no diff has a line for it to sit on.
        return "", None

    return normalise(path), _line_of(test, absolute)


def _source_file(suite, roots):
    """The file a suite names, found under the first root that holds it."""
    name = normalise(str(suite or "").split("::", 1)[0].strip())
    if not name or not name.endswith(".py"):
        return ""

    for root in roots:
        candidate = os.path.join(root, name)
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)

    return ""


def _line_of(test, path):
    """The line to put the annotation on, or None to leave it on the file.

    The traceback comes first: an ERROR carries one, and the frame it ends
    at is where the run actually came apart - a fixture two files away, as
    often as not. A FAIL carries only its assertion lines, so the test's own
    `def` is the honest answer for it.
    """
    for match in reversed(list(_FRAME.finditer(test["message"] or ""))):
        if os.path.basename(match.group("path")) == os.path.basename(path):
            return int(match.group("line"))

    return _def_line(path, test["test"])


def _def_line(path, name):
    """The line a test function is defined on, or None when it is not found."""
    # A parametrised test is listed as "test_add[2-3]" and defined as
    # "test_add"; a class's test is listed under its own name either way.
    bare = str(name).split("[", 1)[0].strip()
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", bare):
        return None

    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except (IOError, OSError):
        return None

    found = re.search(r"^[ \t]*(?:async[ \t]+)?def[ \t]+%s[ \t]*\(" % re.escape(bare),
                      text, re.MULTILINE)
    if not found:
        return None

    return text.count("\n", 0, found.start()) + 1


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
                           % (_trim(round(rate, 2)), _trim(minimum)))

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


def _one_of(value, *allowed):
    """A choice input, or the first choice when it is empty or unknown.

    An unknown value is warned about rather than fatal: the action checks
    its own choice inputs before pytest runs, so anything reaching here came
    from a workflow calling this helper directly.
    """
    chosen = str(value or "").strip().lower()
    if not chosen:
        return allowed[0]

    if chosen not in allowed:
        warn("%r is not one of %s - reading it as %r."
             % (value, ", ".join(allowed), allowed[0]))
        return allowed[0]

    return chosen


def _signed(value):
    """A delta, for a workflow to read. Empty when there is no number."""
    if value is None:
        return ""

    rounded = round(value, 2)
    return "%s%s" % ("+" if rounded > 0 else "", _trim(rounded))


def _roots():
    """Where a test file named in the report might be found on this runner.

    The working directory first - the report's paths are relative to the
    rootdir pytest ran in, which is at or below it - and the checkout after,
    for a run whose report was written somewhere else entirely.

    The checkout comes last on purpose: `locate` takes the last root as what
    an annotation's path is relative to, which is what GitHub addresses a
    diff by.
    """
    here = os.getcwd()
    workspace = os.environ.get("GITHUB_WORKSPACE")

    if not workspace or os.path.abspath(workspace) == os.path.abspath(here):
        return [here]

    return [here, workspace]


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

    options = set(re.findall(r"--[a-z0-9][a-z0-9-]*", help_text))

    # A probe that did not actually list the plugin's options - pytest is
    # missing, the plugin failed to load, --help errored - would otherwise
    # read as "this plugin supports nothing" and drop every flag. The core
    # flag is the evidence that the listing is real.
    if "--html-report" not in options:
        return None

    return options


def build_args(env, report_path, help_text=None):
    """The pytest argument list, from the action's inputs in `env`."""
    known = supported(help_text)
    dropped = []

    def take(flag, announce=True):
        if known is None or flag in known:
            return True

        # Only worth saying when the caller asked for it: this action adds
        # --report-open of its own accord, and an older plugin simply not
        # having it is not something anybody needs telling.
        if announce:
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
    wanted = str(env.get("PHR_REPORT_OPEN", "")).strip()
    if take("--report-open", announce=bool(wanted)):
        args.append("--report-open=%s" % (wanted or "none"))

    for line in str(env.get("PHR_TESTS", "")).splitlines():
        line = line.strip()
        if line:
            args.append(line)

    extra = str(env.get("PHR_PYTEST_ARGS", "")).strip()
    if extra:
        import shlex

        try:
            args.extend(shlex.split(extra))
        except ValueError as error:
            raise Unusable("pytest-args could not be read as a command line "
                           "(%s): %s" % (error, extra))

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

    if report_dir == os.path.abspath("."):
        warn("report-path %r puts the report in the working directory itself. "
             "Everything in it - your whole checkout - is what gets uploaded as "
             "the artifact. Name a folder, such as 'report', to upload just the "
             "report." % raw)

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
    write_output("html-report", reescape(expanded))

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
    restored = os.path.join(directory, JSON_NAME)

    if options.previous:
        stamp = ""
        try:
            with open(restored, encoding="utf-8") as handle:
                stamp = str((json.load(handle) or {}).get("start_time", ""))
        except Exception:
            stamp = ""

        with open(options.previous, "w", encoding="utf-8") as handle:
            handle.write(stamp)

    # The build the cache restored is this run's predecessor on this branch,
    # and it is about to be overwritten by the run itself. Kept aside here so
    # the summary can say what changed between the two - which is the whole
    # of the comparison on a push, where there is no base branch to fetch.
    if options.baseline_out and os.path.isfile(restored):
        try:
            import shutil

            shutil.copyfile(restored, options.baseline_out)
        except (IOError, OSError) as error:
            warn("the restored build could not be kept for comparison (%s); "
                 "this run will be reported on its own." % error)

    report = os.path.join(directory, options.report_name)
    if os.path.isfile(restored) and not os.path.isfile(report):
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

            if not isinstance(data, dict):
                raise ValueError("not an object")

            if str(data.get("status", "")).upper() not in ("PASS", "FAIL"):
                raise ValueError("no usable status")

            # The plugin walks these without guarding the walk, so a build
            # that is shaped wrong here takes the whole report with it.
            if not isinstance(data.get("status_list"), dict):
                raise ValueError("no status_list")

            suites = (data.get("content") or {}).get("suites")
            if not isinstance(suites, (dict, list)):
                raise ValueError("no suites")
        except Exception:
            spoiled = path + ".unreadable"
            try:
                # Windows refuses a rename onto an existing name, and a
                # re-run against the same restored cache would hit exactly
                # that. Failing here would cost the whole report.
                if os.path.exists(spoiled):
                    os.remove(spoiled)

                os.rename(path, spoiled)
            except OSError:
                try:
                    os.remove(path)
                except OSError:
                    warn("%s could not be read and could not be moved out of "
                         "the way; the report may not be written." % path)
                    continue

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


def _previous(path):
    """The start_time cmd_prime recorded for a restored build, if any."""
    if not path:
        return None

    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip() or None
    except (IOError, OSError):
        return None


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
    run = Run.load(options.json, previous=_previous(options.previous))
    counts = run.counts

    wall = _optional_float(options.wall_clock, "wall-clock")

    baseline = Run(None, "")
    if _one_of(options.compare, "auto", "none") != "none":
        baseline = load_baseline(options.baseline_json, options.baseline_zip)

    comparison = compare(run, baseline) if (run.found and baseline.found) else None

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
        # Read out of the report rather than derived, so a link either goes
        # to the row it names or is not offered at all.
        "anchors": anchors(options.report_file) if options.pages_url else {},
        "comparison": comparison,
        "comparison_label": options.baseline_label,
        "comparison_url": options.baseline_url,
    }

    markdown = render(run, context)

    for key, label, _ in STATUS_LABELS:
        write_output({"pass": "passed", "fail": "failed", "error": "errors",
                      "skip": "skipped", "xpass": "xpassed", "xfail": "xfailed",
                      "rerun": "rerun"}[key], counts[key])

    write_output("total", run.total)
    write_output("suites", run.suites)
    write_output("status", run.status)
    write_output("pass-rate", "" if run.pass_rate is None
                 else _trim(round(run.pass_rate, 2)))
    write_output("tests-duration", run.duration)
    write_output("wall-clock", "" if wall is None else _trim(wall))
    write_output("coverage", "" if not run.coverage
                 else _trim(_float(run.coverage.get("percent"))))
    write_output("report-found", "true" if run.found else "false")
    write_output("flaky", len(run.flaky()))
    write_output("summary", markdown)

    # Empty rather than 0 when there was nothing to compare against: a
    # workflow reading "0 new failures" would be told a comparison happened
    # and found nothing, which is a different thing from no comparison.
    write_output("baseline-found", "true" if comparison else "false")
    write_output("new-failures", len(comparison["new_failures"]) if comparison else "")
    write_output("fixed", len(comparison["fixed"]) if comparison else "")
    write_output("still-failing", len(comparison["still_failing"]) if comparison else "")
    write_output("pass-rate-delta", _signed(comparison["pass_rate"][2]) if comparison else "")
    write_output("coverage-delta", _signed(comparison["coverage"][2]) if comparison else "")

    if _flag(options.annotations):
        annotate(run, comparison, options, _roots())

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
        # Flattened: a reason can carry a path, and a blockquote ends at the
        # first line that is not one.
        flat = [" ".join(reason.split()) for reason in reasons]
        write_summary("\n> [!CAUTION]\n> " + "\n> ".join(flat) + "\n")

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
    prime.add_argument("--previous", default="",
                       help="where to record the restored build, so the "
                            "summary can tell it from this run's")
    prime.add_argument("--baseline-out", default="",
                       help="where to keep the restored build itself, so this "
                            "run can be compared against the last one")
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
    # Deliberately not type=int: a workflow forwarding an input it does not
    # have passes an empty string, and _limit() reads that as "use the
    # default" rather than as a reason to abandon the run.
    summarize.add_argument("--failure-limit", default="10")
    summarize.add_argument("--suite-limit", default="20")
    summarize.add_argument("--slowest-limit", default="5")
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
    summarize.add_argument("--previous", default="",
                           help="a file cmd_prime wrote naming the build the "
                                "cache restored, so it is not mistaken for "
                                "this run's")
    summarize.add_argument("--report-file", default="",
                           help="the HTML report, read for the anchors its "
                                "rows carry so the summary can link to them")
    summarize.add_argument("--annotations", default="true")
    summarize.add_argument("--annotation-limit", default=str(ANNOTATION_LIMIT))
    summarize.add_argument("--compare", default="auto")
    summarize.add_argument("--baseline-json", default="",
                           help="an output.json to compare this run against")
    summarize.add_argument("--baseline-zip", default="",
                           help="a report artifact to take that output.json out of")
    summarize.add_argument("--baseline-label", default="",
                           help="what to call the baseline in the summary")
    summarize.add_argument("--baseline-url", default="",
                           help="where the baseline run can be read")
    summarize.set_defaults(handler=cmd_summarize)

    options = parser.parse_args(argv)
    if not getattr(options, "handler", None):
        parser.print_help()
        return 2

    try:
        return options.handler(options)
    except Unusable as error:
        fail(str(error))
        return _verdict_on_error(options, 2)
    except Exception as error:
        import traceback

        traceback.print_exc()
        fail("%s: %s" % (type(error).__name__, error))
        return _verdict_on_error(options, 3)


def _verdict_on_error(options, code):
    """Leave a failing verdict behind, whatever went wrong reaching one.

    The step that runs this reads the verdict back out of an output rather
    than out of an exit code, so a crash that writes no verdict is a crash
    that would otherwise be read as "nothing to fail on".
    """
    if getattr(options, "handler", None) is cmd_summarize:
        write_output("gate-passed", "false")
        write_output("gate-reasons",
                     "the summary could not be produced, so this run was never "
                     "checked - see the error above")

    return code


if __name__ == "__main__":
    sys.exit(main())
