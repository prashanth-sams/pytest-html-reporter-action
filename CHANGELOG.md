# Changelog

All notable changes to this action. The plugin it wraps has a changelog of its
own, at
[pytest-html-reporter/CHANGELOG.txt](https://github.com/prashanth-sams/pytest-html-reporter/blob/master/CHANGELOG.txt).

This project follows [semantic versioning](https://semver.org). The `v1` tag
moves to the newest `v1.x.y` release, so `@v1` picks up fixes; pin `@v1.0.0` to
hold still.

## 1.0.1

- Annotates the line each failing test is written at, so a failure is marked
  on the diff of the pull request that caused it rather than only in the
  summary. A flake - a test that only passed on a retry - is annotated as a
  warning. `annotations: 'false'` turns it off, `annotation-limit` caps it.
- Compares the run with the last successful run of the same workflow on the
  base branch, and reports what changed: newly failing tests, fixed ones, and
  how the pass rate and coverage moved. The baseline is the report artifact
  that run already uploaded, so nothing is stored for it. Needs `actions: read`;
  without it the comparison is skipped with a warning rather than failing the
  run. `compare: 'none'` turns it off, `baseline-json` supplies your own.
- Falls back to the build the history cache restored when there is no base
  branch artifact to reach, so a push is compared with the last push.
- Counts flaky tests apart from failures, as a `flaky` output and a section of
  its own in the summary. A run that needed three attempts to go green is not
  the same as one that did not.
- Links every failure in the summary and the comment to that test's own row in
  the report, when `report-url` says where the report is published. The anchors
  are read back out of the report rather than derived, so a link either lands on
  the row it names or is not offered.
- New outputs: `flaky`, `baseline-found`, `new-failures`, `fixed`,
  `still-failing`, `pass-rate-delta`, `coverage-delta` and `baseline-url`.

## 1.0.0

First release.

- Runs pytest with `pytest-html-reporter` and publishes the result to the job
  summary, a sticky pull request comment, a workflow artifact and step outputs.
- Every one of the plugin's report options is exposed as an input, and left out
  of the command when empty so the plugin's defaults and your `pytest.ini` keys
  still apply.
- Only passes a flag the installed plugin actually has, warning about the rest,
  so pinning an older `pytest-html-reporter` degrades instead of failing the run
  with a usage error.
- Carries build history between runs with `history: 'true'`, so Trends,
  Archives and Analytics have more than one build to read.
- Gates on `min-pass-rate` and `min-coverage`, and a threshold that cannot be
  measured fails rather than passing by default.
- Explains pytest's exit codes, and quotes the usage error back out of the log
  when pytest refused an argument.
