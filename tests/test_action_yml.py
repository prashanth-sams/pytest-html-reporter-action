"""Checks on action.yml itself.

Most of what goes wrong with a composite action goes wrong at load time, in
every consumer's workflow at once, and none of it is caught by running the
helper's unit tests. So the metadata is checked here: the shape the runner
requires, the wiring between steps and outputs, and the promises the README
makes about both.
"""

import os
import re

import pytest

yaml = pytest.importorskip("yaml", reason="pyyaml is needed to read action.yml")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# github.com/actions/runner: the colours the Marketplace accepts.
BRANDING_COLORS = {"white", "yellow", "blue", "green", "orange", "red", "purple", "gray-dark"}

TOP_LEVEL = {"name", "author", "description", "inputs", "outputs", "runs", "branding"}


@pytest.fixture(scope="module")
def action():
    with open(os.path.join(ROOT, "action.yml"), encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@pytest.fixture(scope="module")
def readme():
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture(scope="module")
def steps(action):
    return action["runs"]["steps"]


# ---------------------------------------------------------------------------
# the shape the runner insists on
# ---------------------------------------------------------------------------

def test_only_keys_the_metadata_schema_allows(action):
    assert set(action) <= TOP_LEVEL, "unknown top-level key in action.yml"


def test_it_is_a_composite_action(action):
    assert action["runs"]["using"] == "composite"


def test_every_run_step_declares_its_shell(steps):
    # The single most common way to publish an action that fails to load for
    # everyone who uses it.
    missing = [step.get("name") for step in steps if "run" in step and not step.get("shell")]

    assert missing == []


def test_no_step_is_both_a_run_and_a_uses(steps):
    both = [step.get("name") for step in steps if "run" in step and "uses" in step]

    assert both == []


def test_every_step_is_named(steps):
    assert all(step.get("name") for step in steps)


def test_no_input_declares_a_type(action):
    # `type:` is valid for workflow_call inputs and invalid here. It fails
    # schema validation at publish time rather than when it is written.
    typed = [name for name, spec in action["inputs"].items() if "type" in (spec or {})]

    assert typed == []


def test_every_input_has_a_description_and_a_default(action):
    for name, spec in action["inputs"].items():
        assert (spec or {}).get("description"), "%s has no description" % name
        assert "default" in (spec or {}), "%s has no default" % name


def test_every_output_has_a_description(action):
    for name, spec in action["outputs"].items():
        assert (spec or {}).get("description"), "%s has no description" % name


def test_the_branding_is_one_the_marketplace_takes(action):
    branding = action["branding"]

    assert branding["color"] in BRANDING_COLORS
    assert re.match(r"^[a-z0-9-]+$", branding["icon"])


def test_the_action_is_named_for_the_plugin(action):
    assert action["name"] == "pytest-html-reporter"


# ---------------------------------------------------------------------------
# the wiring between the steps
# ---------------------------------------------------------------------------

def references(text):
    """Every expression in a value, however deeply nested.

    Two forms count: a `${{ ... }}` interpolation anywhere, and the whole of
    an `if:`, which is a bare expression with no braces around it.
    """
    if isinstance(text, dict):
        found = [item for value in text.values() for item in references(value)]
        if isinstance(text.get("if"), str):
            found.append(text["if"])
        return found

    if isinstance(text, list):
        return [item for value in text for item in references(value)]

    return re.findall(r"\$\{\{(.*?)\}\}", str(text), re.DOTALL)


def test_every_input_referenced_is_declared(action):
    declared = set(action["inputs"])
    used = set()

    for expression in references(action["runs"]) + references(action["outputs"]):
        used.update(re.findall(r"inputs\.([a-z0-9-]+)", expression))

    assert used - declared == set()


def test_every_step_output_read_comes_from_a_step_that_exists(action, steps):
    ids = {step["id"] for step in steps if step.get("id")}
    read = set()

    for expression in references(action["runs"]) + references(action["outputs"]):
        read.update(re.findall(r"steps\.([a-z0-9_-]+)\.out", expression))

    assert read - ids == set()


def test_an_output_is_never_read_before_its_step_has_run(action, steps):
    """A step cannot read an output from a step that comes after it."""
    order = {}
    for index, step in enumerate(steps):
        if step.get("id"):
            order[step["id"]] = index

    for index, step in enumerate(steps):
        for expression in references(step):
            for step_id in re.findall(r"steps\.([a-z0-9_-]+)\.out", expression):
                assert order[step_id] < index, (
                    "%r reads an output of %r, which runs later"
                    % (step.get("name"), step_id))


def test_every_declared_input_is_actually_used(action):
    used = set()
    for expression in references(action["runs"]):
        used.update(re.findall(r"inputs\.([a-z0-9-]+)", expression))

    unused = set(action["inputs"]) - used

    assert unused == set(), "declared but never read: %s" % sorted(unused)


def test_inputs_reach_bash_through_env_never_through_interpolation(steps):
    """An expression spliced into a run: body is a shell injection.

    `${{ inputs.pytest-args }}` written straight into a script becomes shell
    source. Every input has to arrive as an environment variable instead.
    """
    for step in steps:
        if "run" not in step:
            continue

        for expression in re.findall(r"\$\{\{(.*?)\}\}", step["run"], re.DOTALL):
            assert "inputs." not in expression, (
                "%r interpolates %s into its script - pass it through env: instead"
                % (step.get("name"), expression.strip()))


def test_the_action_only_calls_first_party_actions(steps):
    for step in steps:
        if "uses" not in step:
            continue

        assert step["uses"].startswith("actions/"), (
            "%s is not a first-party action" % step["uses"])
        assert "@" in step["uses"], "%s is not pinned" % step["uses"]


# ---------------------------------------------------------------------------
# the promises the README makes
# ---------------------------------------------------------------------------

def documented(readme):
    """Everything the README names in a table's first column as `code`."""
    return set(re.findall(r"^\| `([a-z0-9-]+)`", readme, re.MULTILINE))


def test_every_input_is_documented(action, readme):
    undocumented = set(action["inputs"]) - documented(readme)

    assert undocumented == set(), "not in the README: %s" % sorted(undocumented)


def test_the_readme_documents_no_input_that_does_not_exist(action, readme):
    # The other direction: a table row left behind by a rename.
    inputs = set(action["inputs"])
    outputs = set(action["outputs"])
    # Outputs sharing a row, and the plugin flags, are named in the same style.
    known = inputs | outputs | {name.lstrip("-") for name in _flags(action)}

    invented = {name for name in documented(readme) if name not in known}

    assert invented == set(), "documented but not declared: %s" % sorted(invented)


def _flags(action):
    with open(os.path.join(ROOT, "scripts", "phr.py"), encoding="utf-8") as handle:
        return set(re.findall(r'"(--[a-z-]+)"', handle.read()))


def test_the_defaults_in_the_readme_are_the_real_defaults(action, readme):
    """A default quoted in the README has to be the one in action.yml."""
    rows = re.findall(r"^\| `([a-z0-9-]+)` \| `?([^|`]*)`? \|", readme, re.MULTILINE)

    for name, stated in rows:
        if name not in action["inputs"]:
            continue

        if stated.strip().startswith("--"):
            continue  # the plugin-flag table, whose second column is a flag

        stated = stated.strip().strip("`").strip()
        actual = str(action["inputs"][name].get("default", ""))

        if stated in ("", "''"):
            assert actual == "", "%s: README says empty, action.yml says %r" % (name, actual)
        elif stated.startswith("${{"):
            continue
        else:
            assert stated == actual, (
                "%s: README says %r, action.yml says %r" % (name, stated, actual))


def test_the_examples_all_parse():
    directory = os.path.join(ROOT, "examples")

    for name in sorted(os.listdir(directory)):
        with open(os.path.join(directory, name), encoding="utf-8") as handle:
            workflow = yaml.safe_load(handle)

        assert workflow.get("jobs"), "%s declares no jobs" % name
