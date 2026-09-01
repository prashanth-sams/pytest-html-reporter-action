#!/usr/bin/env bash
# Build a small pytest project for the self-test workflow to point the action
# at. `make-project.sh <dir> pass|fail` - the fail flavour adds a failure, an
# error and a skip, so the summary and the counts have something to show.
set -euo pipefail

directory="${1:?usage: make-project.sh <dir> <pass|fail>}"
flavour="${2:-pass}"

rm -rf "$directory"
mkdir -p "$directory/tests" "$directory/src"

cat > "$directory/src/calc.py" <<'PY'
def add(a, b):
    return a + b


def describe(value):
    if value > 0:
        return "positive"
    if value < 0:
        return "negative"
    return "zero"
PY

cat > "$directory/tests/conftest.py" <<'PY'
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
PY

cat > "$directory/tests/test_calc.py" <<'PY'
from calc import add, describe


def test_add():
    assert add(2, 3) == 5


def test_add_is_commutative():
    assert add(2, 3) == add(3, 2)


def test_describe():
    assert describe(1) == "positive"
PY

if [ "$flavour" = "fail" ]; then
  cat > "$directory/tests/test_trouble.py" <<'PY'
import pytest


def test_fails():
    assert {"status": "ok"} == {"status": "down"}


@pytest.mark.skip(reason="needs a payment sandbox")
def test_skipped():
    pass


@pytest.fixture
def unbuildable():
    raise RuntimeError("this fixture cannot be built")


def test_errors(unbuildable):
    pass
PY
fi

echo "built $flavour project in $directory/"
