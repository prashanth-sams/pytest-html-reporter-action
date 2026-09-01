# Contributing

## Running the tests

```console
$ python -m pip install pytest pyyaml
$ python -m pytest
```

`tests/test_phr.py` covers the helper — the counts, the pass rate, the
markdown, the thresholds and the command it builds. `tests/test_action_yml.py`
covers the metadata: the shape the runner requires, the wiring between steps,
and whether the README still describes the inputs that exist.

That last one is the reason a new input needs a README row in the same commit.
The test will tell you.

## Running the action

`.github/workflows/self-test.yml` runs the action against a project built by
`tests/fixtures/make-project.sh`, on Linux, macOS and Windows, and asserts on
the outputs. That is the only place the composite wiring is exercised for real,
so a change to `action.yml` needs a push to see it work.

To try it by hand:

```console
$ bash tests/fixtures/make-project.sh /tmp/sample fail
$ cd /tmp/sample
$ pip install pytest-html-reporter
$ python /path/to/scripts/phr.py resolve --path report
$ pytest tests/ --html-report=report --report-open=none
$ python /path/to/scripts/phr.py summarize --json report/output.json --exit-code 1
```

## Linting

```console
$ python scripts/extract_shell.py action.yml .shellcheck
$ shellcheck --shell=bash .shellcheck/*.sh
$ yamllint -c .yamllint.yml action.yml .github/workflows examples
```

`shellcheck` cannot read YAML, so `extract_shell.py` lifts the `run:` blocks
out first. Most of this action is that bash, so it is worth linting.

## House style

The helper is stdlib-only and stays that way: it runs on whatever Python the
workflow set up, and an action that installs its own dependencies to print a
table is an action that breaks on somebody's locked-down runner.

Comments explain *why*, not *what*. There are a number of sharp edges in the
plugin's behaviour that this action works around — those workarounds each carry
a comment saying what would otherwise go wrong, because without it the next
reader deletes them.

## Releasing

See [RELEASING.md](RELEASING.md).
