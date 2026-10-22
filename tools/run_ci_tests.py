"""Run the modules of the CI workflow from the root of the repository.

The tests read files of the repository with relative paths, so the working
directory has to be the root of the repository.

    python tools/run_ci_tests.py
"""
from __future__ import annotations

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "ci.yml")


def modules():
    names = []
    with open(WORKFLOW, "r", encoding="utf-8") as handle:
        for line in handle:
            match = re.match(r"^\s+(test_[a-z0-9_]+)\s*\\?\s*$", line)
            if match:
                names.append(match.group(1))
    return names


def main() -> int:
    os.chdir(ROOT)
    sys.path.insert(0, ROOT)
    names = modules()
    suite = unittest.TestLoader().loadTestsFromNames(names)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    print("modules: %d, tests: %d" % (len(names), result.testsRun))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
