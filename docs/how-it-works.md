# How it works

The action is a composite: fourteen steps in `action.yml`, and one stdlib-only
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
Find a baseline         actions/github-script   (compare: auto)
Summarise the run       output.json -> outputs, job summary, comment body,
                        annotations, and what changed since the baseline
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

## Why the baseline is an artifact rather than a store

Comparing a run with the base branch needs a copy of what the base branch did.
Keeping one would mean a cache, a branch, a gist or a service — something to
write, to expire, to get out of step with the branch it claims to describe.

There is already a copy: the report artifact this action uploaded on the last
successful run of this same workflow over there. So the comparison asks the API
which run that was, downloads the artifact it left, and reads the `output.json`
out of the zip. Nothing is stored, nothing expires that GitHub was not already
expiring, and a repository that has never run the action simply has no baseline
and is told so.

It costs one permission — `actions: read`, to list and download another run's
artifacts — and every failure along the way is a warning. A comparison is a
nicety on top of the report; a repository that has not granted the permission
should still get the report.

Where there is no base branch to reach and `history` is on, the build the cache
restored stands in: it is this branch's previous run, and `prime` copies it
aside before pytest overwrites it.

## Why the anchors are read back out of the report

Each failure in the summary links to that test's own row in the published
report. The row's id is built from the test's **node id** — and `output.json`
records the suite and the test name, not the node id. For a test inside a class
those are different: the report lists `test_login` where pytest wants
`TestAuth::test_login`, and an id derived from the listed name resolves to
nothing.

So the ids are read back out of the HTML the plugin has just written, matched
to a run by the suite-and-test index each row carries. An id that is not the
shape the plugin hands out is refused rather than pasted into an `href`, and a
report that cannot be read costs the reader a link rather than sending them
somewhere wrong.

## Why a flake is counted apart from a failure

A test carrying a rerun that ends the run green failed at least once and passed
at least once, in one run, against one commit. It is not a failure — nothing is
blocked — and it is not a pass either, and rolling it into the pass count is how
it goes unlooked-at for a year.

So it is a `flaky` output, a section of its own in the summary, and a warning
annotation rather than an error one. A test that was retried and failed every
time is a plain failure; it gave one answer, just slowly.

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
