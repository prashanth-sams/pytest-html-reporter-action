# Changelog

All notable changes to this action. The plugin it wraps has a changelog of its
own, at
[pytest-html-reporter/CHANGELOG.txt](https://github.com/prashanth-sams/pytest-html-reporter/blob/master/CHANGELOG.txt).

This project follows [semantic versioning](https://semver.org). The `v1` tag
moves to the newest `v1.x.y` release, so `@v1` picks up fixes; pin `@v1.0.0` to
hold still.

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
