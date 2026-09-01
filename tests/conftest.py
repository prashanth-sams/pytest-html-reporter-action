import os
import sys

# The helper lives in scripts/, which is not a package - the action calls it
# by path. Put it on sys.path so the tests can import it the same way.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
