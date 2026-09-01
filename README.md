<h1 align="center">pytest-html-reporter</h1>

<p align="center">
  The GitHub Action for
  <a href="https://github.com/prashanth-sams/pytest-html-reporter">pytest-html-reporter</a> —
  run your tests, and get the report where people will actually look at it.
</p>

<p align="center">
  <a href="https://github.com/prashanth-sams/pytest-html-reporter-action/actions"><img alt="CI" src="https://github.com/prashanth-sams/pytest-html-reporter-action/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://pypi.org/project/pytest-html-reporter/"><img alt="PyPI" src="https://badge.fury.io/py/pytest-html-reporter.svg"></a>
  <a href="LICENSE"><img alt="MIT" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
</p>

---

One step runs pytest, builds the report, and puts the result in four places at
once: the **job summary** on the run page, a **sticky comment** on the pull
request, a **downloadable artifact**, and a set of **step outputs** for
whatever you want to do next.

```yaml
- uses: prashanth-sams/pytest-html-reporter-action@v1
  with:
    tests: tests/
```

<br>

## Contents

- [What you get](#what-you-get)
- [Quick start](#quick-start)
- [Recipes](#recipes)
  - [Comment on the pull request](#comment-on-the-pull-request)
  - [Trends across builds](#trends-across-builds)
  - [Coverage](#coverage)
  - [Fail on a threshold](#fail-on-a-threshold)
  - [A matrix](#a-matrix)
  - [Publish to GitHub Pages](#publish-to-github-pages)
  - [Use the outputs](#use-the-outputs)
- [Inputs](#inputs)
- [Outputs](#outputs)
- [Permissions](#permissions)
- [Things worth knowing](#things-worth-knowing)
- [Troubleshooting](docs/troubleshooting.md)
- [How it works](docs/how-it-works.md)
- [Contributing](#contributing)

<br>

## What you get

**In the job summary** — the headline, the counts, every failure with its
message, the suites ranked by damage, the slowest tests, and coverage when the
run measured any. No clicking through to an artifact to find out what broke.

**On the pull request** — the same summary as one comment that gets *updated*
on every push rather than a new comment each time.

**As an artifact** — the full interactive HTML report: Overview, Trends,
Analytics, Test Steps, Archives, Screenshots, Attachments, Test Coverage.

**As outputs** — `passed`, `failed`, `pass-rate`, `coverage`, `status` and a
dozen more, so a later step can post to Slack, open an issue, or gate a deploy.

<br>

## Quick start

```yaml
name: tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: prashanth-sams/pytest-html-reporter-action@v1
        with:
          python-version: '3.12'
          requirements: requirements.txt
          tests: tests/
          title: Nightly regression
```

That installs Python, installs your requirements and the plugin, runs pytest,
writes the job summary, uploads the report as an artifact, and fails the job if
any test failed.

<br>

## Recipes

### Comment on the pull request

```yaml
permissions:
  contents: read
  pull-requests: write

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: prashanth-sams/pytest-html-reporter-action@v1
        with:
          tests: tests/
          comment: 'true'
          comment-mode: on-failure   # or `always`
```

The comment carries a hidden marker, so the next push edits it rather than
adding another. A `pull_request` run from a **fork** gets a read-only token and
cannot comment — the action logs a warning and carries on rather than turning
the run red. See [examples/fork-pr.yml](examples/fork-pr.yml) for the
`workflow_run` pattern that does comment on fork pull requests.

### Trends across builds

The report's Trends, Archives and Analytics tabs are built from previous
builds, which a fresh runner does not have. `history: 'true'` carries them
between runs with the Actions cache:

```yaml
- uses: prashanth-sams/pytest-html-reporter-action@v1
  with:
    tests: tests/
    history: 'true'
    archive-days: '30'    # and prune anything older
```

Keep a limit on it. Every retained build costs roughly 5KB of the page, so an
hourly run with no limit reaches a multi-megabyte report within a couple of
months. `archive-count` caps the number of builds, `archive-days` caps their
age, and `archive-since` cuts everything before a date; set several and a build
has to satisfy all of them to be kept.

> `archive-count: '0'` does not mean "no limit" — it **deletes** the whole
> archive and hides the Archives section. Leave the input empty for no limit.

### Coverage

Coverage comes from whatever measured it. The simplest case needs nothing from
this action beyond the `--cov` flag:

```yaml
- uses: prashanth-sams/pytest-html-reporter-action@v1
  with:
    tests: tests/
    extra-packages: pytest-cov
    pytest-args: --cov=src
```

The plugin picks up the live pytest-cov data, or a `coverage.json` /
`coverage.xml` sitting in the report directory, the repository root, or the
working directory. Only a `.coverage` data file or a file under some other name
needs `report-coverage-file` — and note that naming a file there is *final*: if
it cannot be read, the Coverage tab is blank, and the coverage this run
measured is not used instead.

If your tests run in one job and the report in another, pass a Cobertura
`coverage.xml` between them: it parses with the standard library, so the
reporting job needs no `coverage` package installed.

### Fail on a threshold

```yaml
- uses: prashanth-sams/pytest-html-reporter-action@v1
  with:
    tests: tests/
    min-pass-rate: '95'
    min-coverage: '80'
```

`pass-rate` is `passed / (passed + failed + errors)`. Skipped, xfailed and
xpassed tests are in neither half — none of them is a pass-or-fail signal, and
counting them would quietly move the threshold you set.

A threshold that cannot be measured fails loudly rather than passing by
default: `min-coverage` on a run that produced no coverage is an error, not a
free pass.

### A matrix

`actions/upload-artifact` refuses two artifacts of the same name in one run, so
give each job its own:

```yaml
strategy:
  matrix:
    os: [ubuntu-latest, macos-latest, windows-latest]
    python: ['3.9', '3.13']

steps:
  - uses: actions/checkout@v4
  - uses: prashanth-sams/pytest-html-reporter-action@v1
    with:
      python-version: ${{ matrix.python }}
      tests: tests/
      artifact-name: report-${{ matrix.os }}-py${{ matrix.python }}
      build-info: |
        os=${{ matrix.os }}
        python=${{ matrix.python }}
```

### Publish to GitHub Pages

Deploying to Pages needs its own job with its own environment, so it cannot
live inside the action. The action gets you as far as the Pages artifact:

```yaml
- uses: prashanth-sams/pytest-html-reporter-action@v1
  with:
    tests: tests/
    pages-artifact: 'true'
```

The deploy job is three more lines — see
[examples/pages.yml](examples/pages.yml) for the whole workflow.

### Use the outputs

```yaml
- uses: prashanth-sams/pytest-html-reporter-action@v1
  id: report
  with:
    tests: tests/
    fail-on-error: 'false'      # decide for yourself, below

- name: Tell the team
  if: steps.report.outputs.status == 'FAIL'
  run: |
    gh issue create \
      --title "Nightly run: ${{ steps.report.outputs.failed }} failing" \
      --body "${{ steps.report.outputs.summary }}"
  env:
    GH_TOKEN: ${{ github.token }}
```

<br>

## Inputs

Every input is a string, and every one has a default, so you can set as few or
as many as you like. Booleans are the strings `'true'` and `'false'`.

### What to run

| Input | Default | Description |
| --- | --- | --- |
| `tests` | `''` | Test paths for pytest, one per line. Empty lets pytest pick its own targets. |
| `pytest-args` | `''` | Extra pytest arguments, parsed the way a shell would (quotes respected). |
| `working-directory` | `.` | Directory to run pytest in. Every path is resolved against it. |

### The environment

| Input | Default | Description |
| --- | --- | --- |
| `python-version` | `''` | Python to set up with `actions/setup-python`. Empty uses whatever is on the runner. |
| `install` | `true` | Install `pytest-html-reporter` before running. |
| `plugin-version` | `''` | Version specifier, e.g. `0.3.8` or `>=0.3.8`. Empty installs the latest. |
| `requirements` | `''` | Requirements file to install first, e.g. `requirements.txt`. |
| `extra-packages` | `''` | Further pip packages, whitespace separated, e.g. `pytest-xdist pytest-cov`. |

### The report

Each of these maps one-to-one onto a plugin flag, and each is **left out
entirely when empty** — so the plugin's own defaults, and any `report_*` key in
your `pytest.ini`, still apply. Setting one here overrides the ini key.

| Input | Flag | Description |
| --- | --- | --- |
| `report-path` | `--html-report` | Where the report goes. Default `report`. A value containing `.html` names the file; anything else names a directory. `strftime` placeholders are expanded once, before the run. |
| `title` | `--title` | Report title. Shown cut to 20 characters, with the full text as the heading's tooltip. |
| `environment` | `--environment` | Name of the environment under test, e.g. `staging`. |
| `build-info` | `--build-info` | Extra `KEY=VALUE` details, one per line. `#` comments are skipped. |
| `report-links` | `--report-link` | Side-nav links, one `LABEL=URL` per line. Only `http`, `https` and `mailto` links are kept. |
| `archive-count` | `--archive-count` | Builds to keep. Empty keeps every one; `0` deletes them all. |
| `archive-days` | `--archive-days` | Keep only builds from the last N days. Accepts fractions, e.g. `0.5`. |
| `archive-since` | `--archive-since` | Delete builds older than `YYYY-MM-DD` or `'YYYY-MM-DD HH:MM'`. Read in the runner's timezone, which is UTC. |
| `report-logs` | `--report-logs` | Whose captured stdout, stderr and logging to keep — `all`, `failed` or `none`. |
| `report-log-limit` | `--report-log-limit` | Characters of captured output kept per test; `0` keeps everything. |
| `report-attachments` | `--report-attachments` | Whose attachments to keep — `all`, `failed` or `none`. |
| `report-attachment-limit` | `--report-attachment-limit` | Characters kept per attached payload; `0` keeps everything. |
| `report-steps` | `--report-steps` | Whose test steps to keep — `all`, `failed` or `none`. |
| `report-step-limit` | `--report-step-limit` | Steps kept per test; `0` keeps every one. |
| `report-coverage` | `--report-coverage` | Whether to build the Test Coverage tab — `auto` or `none`. |
| `report-coverage-file` | `--report-coverage-file` | Read coverage from this file instead of looking for one. |
| `report-coverage-limit` | `--report-coverage-limit` | Files listed on the Coverage tab; `0` lists every one. |
| `report-open` | `--report-open` | Whether to open the report in a browser. Forced to `none` unless you set it — see [Things worth knowing](#things-worth-knowing). |

### Publishing

| Input | Default | Description |
| --- | --- | --- |
| `job-summary` | `true` | Write the summary to the run's job summary page. |
| `summary-title` | `pytest-html-reporter` | Heading for the summary and the comment. |
| `failure-limit` | `10` | Failures listed before the rest are counted instead. |
| `suite-limit` | `20` | Suites listed in the summary table. |
| `slowest-limit` | `5` | Slowest tests listed; `0` lists none. |
| `report-url` | `''` | Link to the published report, shown in the summary and comment. |
| `comment` | `false` | Post the summary as a sticky pull request comment. |
| `comment-mode` | `always` | `always`, or `on-failure`. |
| `pr-number` | `''` | Pull request to comment on. Only needed when the event carries none — a `workflow_run` job, say. |
| `github-token` | `${{ github.token }}` | Token used to comment. Needs `pull-requests: write`. |
| `upload-artifact` | `true` | Upload the report directory as a workflow artifact. |
| `artifact-name` | `pytest-html-report` | Artifact name. Must be unique within a run. |
| `artifact-retention-days` | `''` | Days to keep it. Empty uses the repository default. |
| `artifact-include-history` | `true` | Include the `archive/` folder of past builds. |
| `artifact-if-no-files-found` | `warn` | `warn`, `error` or `ignore`. |
| `artifact-overwrite` | `false` | Replace an artifact of the same name instead of failing. |
| `pages-artifact` | `false` | Also upload a GitHub Pages artifact for a following deploy job. |
| `history` | `false` | Carry `archive/` between runs with `actions/cache`. |
| `history-key` | `pytest-html-reporter-history` | Cache key prefix for that history. |

### Gates

| Input | Default | Description |
| --- | --- | --- |
| `fail-on-error` | `true` | Fail the job when pytest fails. |
| `fail-on-empty` | `true` | Fail the job when pytest collects no tests at all. |
| `min-pass-rate` | `''` | Fail below this pass rate, e.g. `95`. |
| `min-coverage` | `''` | Fail below this coverage, e.g. `80`. |

<br>

## Outputs

| Output | Example | Description |
| --- | --- | --- |
| `status` | `FAIL` | `PASS`, `FAIL`, or `UNKNOWN` when no report was produced. |
| `total` | `5` | Tests executed. Reruns are attempts, not tests, and are not counted here. |
| `passed` `failed` `errors` `skipped` `xpassed` `xfailed` `rerun` | `2` | The individual counts. |
| `suites` | `2` | Test suites in the run. |
| `pass-rate` | `50` | `passed / (passed + failed + errors)`, as a percentage. Empty when nothing decisive ran. |
| `coverage` | `87.42` | Coverage percentage. Empty when the run produced none. |
| `tests-duration` | `5.35` | Summed test durations, in seconds. The report does not record a run duration, so this leaves out collection, session fixtures and the gaps between tests. |
| `wall-clock` | `74` | Seconds the pytest step took, measured by the action. |
| `exit-code` | `1` | What pytest exited with. |
| `report-file` | `/…/report/pytest_html_report.html` | The generated HTML report. |
| `report-dir` | `/…/report` | The directory holding it. |
| `json-path` | `/…/report/output.json` | The run's machine-readable summary. |
| `summary` | `## ❌ …` | The markdown summary, for reuse in a later step. |
| `artifact-id` `artifact-url` | | From the artifact upload, when one happened. |

<br>

## Permissions

The action needs nothing beyond the default for the basic case. Add what the
features you turn on need:

```yaml
permissions:
  contents: read        # always
  pull-requests: write  # for `comment: 'true'`
```

Since 2023 the default `GITHUB_TOKEN` is read-only, so a missing
`pull-requests: write` is the usual reason a comment does not appear. The
action warns rather than failing when it cannot post.

Pages deployment needs `pages: write` and `id-token: write` — on the *deploy*
job, not this one. See [examples/pages.yml](examples/pages.yml).

<br>

## Things worth knowing

**The report path.** Whether the value names a file or a folder is decided by
whether it contains `.html` *anywhere*, not by its ending. So a folder called
`out.html_v2` makes the plugin treat the whole path as a bare filename and drop
the report in the working directory. The action warns when it sees this.

**Percent signs.** The path is run through `strftime`, so `%p` in
`./100%pass/` becomes `AM` or `PM`. Write `%%` for a literal percent.

**Windows.** The plugin splits the report path on `/` only, so a backslash path
would land somewhere unintended. The action normalises backslashes to forward
slashes on Windows before pytest sees the value.

**pytest exit codes.** Code `4` is a usage error — pytest refused an argument.
That is this action's inputs, or a `report_*` key in your `pytest.ini`; the
message names the *flag*, and the ini keys share their names with the flags.
The action spells this out rather than reporting it as "tests failed".

**Silent ini values.** An invalid value passed by this action fails the run,
loudly. An invalid value in your `pytest.ini` is silently replaced by the
default for most keys. If a setting seems to have no effect, check the ini.

**Opening a browser.** The plugin's default is to open the finished report,
and it declines on a runner — unless a `pytest.ini` says `report_open = always`,
which skips every check and hands the report to a console browser. The action
passes `--report-open=none` so that cannot happen; set `report-open` yourself
if you have a self-hosted runner with a desktop.

**Running after a failed step.** The action has no post-run hook. If an earlier
step in your job can fail and you still want the report, put `if: always()` on
the step that uses this action.

**Defaults that differ from the plugin's.** `report-path` defaults to `report`
here, where a bare `pytest` run writes into the working directory. A CI job
wants the report, its `output.json` and its `archive/` in one uploadable
folder. Everything else is left to the plugin.

<br>

## Contributing

Bug reports and pull requests are welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md). The action's own tests run with
`pytest tests/`, and `.github/workflows/self-test.yml` runs the action against
itself on Linux, macOS and Windows.

[docs/how-it-works.md](docs/how-it-works.md) explains what each step does and
why, and [docs/troubleshooting.md](docs/troubleshooting.md) covers what usually
goes wrong.

The plugin this wraps lives at
[prashanth-sams/pytest-html-reporter](https://github.com/prashanth-sams/pytest-html-reporter).

<br>

## License

MIT — see [LICENSE](LICENSE).
