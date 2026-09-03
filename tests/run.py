#!/usr/bin/env python3
"""Run every test file in this directory.

    python tests/run.py

test_director.py checks the CONTRACT -- the shape of director.json, and that degenerate input
does not produce NaNs or a crash. test_analysis.py checks the CONTENT -- tempo, section
boundaries and the bar grid, each against a fixture whose answer is known by construction.

Both are dependency-free and both exit non-zero on failure, so this is also the CI command.
The analysis suite synthesises and then analyses about a dozen tracks, so it takes a couple of
minutes on a cold cache and rather less after; the fixtures are cached under _fixtures/.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SUITES = ["test_director.py", "test_analysis.py"]

if __name__ == "__main__":
    failed = []
    for name in SUITES:
        print("=" * 72)
        print(name)
        print("=" * 72)
        r = subprocess.run([sys.executable, os.path.join(HERE, name)] + sys.argv[1:])
        if r.returncode != 0:
            failed.append(name)
    print("=" * 72)
    if failed:
        print("FAILED: " + ", ".join(failed))
        sys.exit(1)
    print("all suites passed")
