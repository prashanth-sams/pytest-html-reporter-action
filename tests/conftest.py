import os
import sys

import pytest

# The helper lives in scripts/, which is not a package - the action calls it
# by path. Put it on sys.path so the tests can import it the same way.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


# The three variables scripts/phr.py reads off a runner. The tests inherit
# them from whatever ran pytest, so on Actions the suite appended four of its
# sample reports to the job summary of the unit job, and `roots()` saw a
# checkout rather than the tmp_path the test was standing in. A test that
# wants one of these sets it itself, which is also the only way the suite
# behaves the same on a runner as it does on a laptop.
RUNNER_ENV = ("GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY", "GITHUB_WORKSPACE")


@pytest.fixture(autouse=True)
def _off_the_runner(monkeypatch):
    for name in RUNNER_ENV:
        monkeypatch.delenv(name, raising=False)
