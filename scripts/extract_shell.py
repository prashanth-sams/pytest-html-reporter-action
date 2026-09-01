#!/usr/bin/env python3
"""Pull the `run:` blocks out of an action or workflow file, for shellcheck.

shellcheck cannot read YAML, and the bash inside a composite action is where
this action does most of its work - so without this the largest part of the
codebase goes unlinted.

    python3 scripts/extract_shell.py action.yml .shellcheck
    shellcheck --shell=bash .shellcheck/*.sh

Each block is written with a `${{ ... }}` expression replaced by a harmless
placeholder, because shellcheck would otherwise read `${{` as a bad
parameter expansion and report nothing else.
"""

import os
import re
import shutil
import sys

import yaml

# GitHub substitutes these before bash ever sees them. A quoted placeholder
# keeps the line syntactically what it will be at run time.
# Non-greedy to the closing braces rather than "anything but a brace": an
# expression holding one - format('{0}/archive', ...) - would otherwise be
# cut in half and leave the tail of it in the script being linted.
EXPRESSION = re.compile(r"\$\{\{.*?\}\}", re.DOTALL)

# The step name goes after "step:" rather than straight after the "#": a step
# called "shellcheck the run blocks" would otherwise be read as a malformed
# shellcheck directive, and the file would fail on its own header.
HEADER = ("#!/usr/bin/env bash\n"
          "# step: %s\n"
          "# Extracted from %s by scripts/extract_shell.py - do not edit.\n\n")


def blocks(document, path):
    """(label, script) for every bash `run:` in a parsed action or workflow."""
    found = []

    def visit(node, trail):
        if isinstance(node, dict):
            script = node.get("run")
            shell = str(node.get("shell") or "bash").lower()

            if isinstance(script, str) and shell in ("bash", "sh"):
                name = str(node.get("name") or node.get("id") or "/".join(trail))
                found.append((name, script))

            for key, value in node.items():
                visit(value, trail + [str(key)])

        elif isinstance(node, list):
            for index, value in enumerate(node):
                visit(value, trail + [str(index)])

    visit(document, [os.path.basename(path)])
    return found


def slug(text, index):
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return "%02d-%s" % (index, cleaned or "block")


def main(argv):
    if len(argv) != 3:
        sys.stderr.write("usage: extract_shell.py <yaml file> <output dir>\n")
        return 2

    source, target = argv[1], argv[2]

    with open(source, encoding="utf-8") as handle:
        document = yaml.safe_load(handle)

    if os.path.isdir(target):
        shutil.rmtree(target)

    os.makedirs(target)

    found = blocks(document, source)
    for index, (name, script) in enumerate(found, start=1):
        path = os.path.join(target, slug(name, index) + ".sh")

        with open(path, "w", encoding="utf-8") as handle:
            handle.write(HEADER % (name, source))
            handle.write(EXPRESSION.sub("expression", script))
            if not script.endswith("\n"):
                handle.write("\n")

    print("wrote %d shell block(s) from %s to %s/" % (len(found), source, target))

    if not found:
        sys.stderr.write("no bash blocks found - is %s the file you meant?\n" % source)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
