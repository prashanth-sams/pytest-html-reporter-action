# Troubleshooting

Most of what goes wrong here is one of a handful of things. This is what they
look like and what to do about them.

## "No `output.json` was written"

The run finished and the plugin never wrote anything. Three causes, in order of
likelihood:

**The plugin is not installed in the environment that ran the tests.** The
action's install step and your test run must use the same Python. If you set
`install: 'false'` because your `requirements.txt` pins it, check that the file
really lists `pytest-html-reporter`. The action logs a warning at install time
when the import fails — look for it above the pytest output.

**pytest failed before any test ran.** A collection error, a missing
dependency, a bad `-k`. The pytest log says which.

**`report-path` points somewhere else.** Check the `report-dir` output against
where you are looking.

## Exit code 4: "a usage error"

pytest refused an argument. This is not a failing test — nothing ran.

The message pytest printed names a **flag**, and you set an **input**. They
match one for one: `--archive-since` is the `archive-since` input. But the same
message appears when the value came from your `pytest.ini`, because the ini
keys share their names with the flags. So check both.

The action quotes the offending line back as an annotation, so you should not
have to go digging in the log for it.

## "the installed pytest-html-reporter has no --report-steps"

You are on a version of the plugin older than the input you set. The action
leaves the flag out rather than failing the run — passing an unknown flag would
abort pytest with a usage error before a single test was collected.

Fix it by upgrading:

```yaml
with:
  plugin-version: '>=0.3.8'
```

Or drop the input.

## The Trends, Archives and Analytics tabs are empty

They read previous builds, and a fresh runner has none. Turn history on:

```yaml
with:
  history: 'true'
```

Then check three things:

- **`report-path` must be fixed, not per-run.** A path with `%Y%m%d` in it
  makes a new folder every run, so every run starts from nothing. Put the date
  in `artifact-name` instead. The action warns when it sees this combination.
- **A matrix needs a `history-key` per cell.** The default key already includes
  the runner OS, but two Python versions writing into one history interleave
  builds that are not comparable.
- **`archive-count: '1'` keeps nothing.** The plugin reads it as "this build and
  no others". `'0'` deletes the archive entirely. Leave it empty for no limit.

Note that the first run with history on still shows one build. It takes two.

## The Coverage tab is empty

The plugin reads whatever measured coverage; it does not measure any itself.

- Install `pytest-cov` and pass `--cov=<your package>` in `pytest-args`, in the
  same run. That is the whole of it for a single job.
- Auto-discovery looks for `coverage.json` or `coverage.xml` in the report
  directory, the repository root and the working directory. A `.coverage` data
  file is *not* discovered — name it with `report-coverage-file`, and install
  `coverage` in that job so it can be read.
- `report-coverage-file` is final. If the file named there cannot be read, the
  tab stays blank and the coverage the run measured is **not** used instead.
  The action warns when this happens.
- There is no freshness check. A `coverage.xml` restored from a cache before
  the tests ran is published as this build's number, silently. Do not cache
  coverage files alongside the report.

## The pull request comment never appears

- **Permissions.** `pull-requests: write` on the job. The default token has
  been read-only since 2023.
- **A fork.** A `pull_request` run from a fork gets a read-only token and
  cannot comment at all. This is deliberate on GitHub's part. Use the
  `workflow_run` pattern in [examples/fork-pr.yml](../examples/fork-pr.yml).
- **The event.** `comment: 'true'` only fires on a `pull_request` event, unless
  you pass `pr-number` explicitly.

The action warns and carries on rather than failing the run, so look for a
warning annotation rather than a red step.

## The report is enormous

Every archived build costs roughly 5KB of the page. An hourly run with no
retention limit reaches several megabytes within a couple of months, and the
page gets slow to open.

Set `archive-days: '30'`, or `archive-count: '50'`. They intersect with
`archive-since` — a build has to satisfy every limit you set to be kept.

`report-log-limit`, `report-attachment-limit` and `report-step-limit` cap what
each test contributes. Note that `0` means *unlimited* for all three, and a
negative value is read as `0` — the action rejects a negative rather than
letting it silently mean the opposite of what it looks like.

## The job went green and no tests ran

That is what `fail-on-empty` is for, and it is on by default — a run that
collects nothing fails. If a cell of your matrix legitimately has no tests, set
`fail-on-empty: 'false'` for it.

## The report landed in the wrong folder

`report-path` is read as a **file** when it contains `.html` anywhere, not just
at the end. So `out.html_v2/report.html` makes the plugin treat the whole
thing as a bare filename and write into the working directory. Rename the
folder. The action warns when it detects this.

Percent signs are `strftime` placeholders: `%p` is AM/PM, so `./100%pass/`
becomes `./100PMass/`. Write `%%` for a literal percent.

## `pip` refuses to install anything

`error: externally-managed-environment`. The runner's Python belongs to its
distribution, and pip will not touch it. This does not happen on GitHub-hosted
runners; on a self-hosted one, set `python-version` so `actions/setup-python`
provides an interpreter of its own:

```yaml
with:
  python-version: '3.12'
```

## Something else

Open an issue with the workflow YAML, the job log, and the `report-dir` and
`status` outputs. If the problem is in the report's *contents* rather than in
getting it built, it probably belongs on
[the plugin](https://github.com/prashanth-sams/pytest-html-reporter/issues).
