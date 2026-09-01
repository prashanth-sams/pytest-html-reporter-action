# How it works

The action is a composite: thirteen steps in `action.yml`, and one stdlib-only
Python helper, `scripts/phr.py`, that does the thinking.

```
Check the inputs        validate before anything expensive happens
Set up Python           actions/setup-python, only when python-version is set
Find the interpreter    "python" or "python3" - settle on one name
Install                 the plugin, your requirements, extra packages
Work out where          resolve report-path exactly as the plugin will
Restore build history   actions/cache/restore   (history: true)
Run pytest              prime, probe, run, capture the exit code
Save build history      actions/cache/save      (history: true)
Upload the report       actions/upload-artifact
Upload a Pages artifact actions/upload-pages-artifact  (pages-artifact: true)
Summarise the run       output.json -> outputs, job summary, comment body
Comment                 actions/github-script   (comment: true)
Decide the job          go red, or do not
```

## Why the path is resolved before pytest runs

`--html-report` is run through `strftime`, so `reports/%H%M/` names a different
folder each minute. If the action expanded it separately from the plugin, a run
crossing a minute boundary would leave the action looking in a folder the
report is not in.

So the action expands it once, up front, and hands pytest the already-concrete
path. The plugin then has no placeholders left to expand, and both ends agree
by construction.

The file-or-folder rule is copied from the plugin rather than reinvented,
including its sharp edge: the value names a file when it contains `.html`
*anywhere*, not when it ends with it.

## Why pytest's flags are probed first

Anyone can pin their own version of the plugin, and the older ones have fewer
options. An unknown flag is not a missing feature — argparse aborts the entire
run with exit code 4 before a single test is collected, naming a flag the user
never typed.

So the action runs `pytest --help` once, and only passes a flag that appears in
it. Anything left out is announced as a warning naming the input. If the probe
itself fails, nothing is filtered: passing the flags and letting pytest object
beats dropping all of them.

## Why history needs more than the archive folder

A build joins the archive when the *next* run rotates its `output.json` into
`archive/` — and the plugin only does that when the previous run's report file
is on disk.

A fresh runner has neither. So the cache carries `output.json` as well as
`archive/`, and the `prime` step stands in an empty report file when it finds a
restored `output.json` without one. Without that placeholder every build
quietly replaces its predecessor and the archive stays empty for ever.

`prime` also quarantines any archived build that cannot be parsed. The plugin
reads the archive without guarding the read, so a single truncated file raises
inside `pytest_terminal_summary` and no report is written at all — and a
restored cache is exactly where such a file comes from.

## Why the exit code decides the job

The plugin calls a run `FAIL` when a suite holds a failure or an error. pytest
has more ways to exit non-zero than that: a strict xfail, `pytest.exit()`, an
interrupted run.

So the exit code is what the job is decided on, and `status` is the report's
headline. When the two disagree the action says so rather than picking one
quietly.

## Why nothing is interpolated into the shell

Every input reaches bash as an environment variable, never as `${{ ... }}`
spliced into a `run:` body — that would make `pytest-args` shell source.
`tests/test_action_yml.py` fails the build if anyone writes one.

Test names and failure messages get the same treatment on the way out: they
come from test code, which on a fork's pull request belongs to somebody else.
Pipes are escaped so a name cannot end a table cell, a line opening with `::`
is broken so it cannot be read as a workflow command, and the `$GITHUB_OUTPUT`
heredoc delimiter is random so no payload can close the block early.
