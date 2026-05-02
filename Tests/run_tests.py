"""
Test runner for the Tengu-Marauder-Stryker test suite.

Usage:
    python Tests/run_tests.py           # run all tests
    python Tests/run_tests.py -v        # verbose output
    python Tests/run_tests.py test_drive  # run one module by name
"""

import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    verbosity = 2 if "-v" in sys.argv else 1

    # Optional: filter to a single module name passed as argument
    name_filter = next(
        (a for a in sys.argv[1:] if not a.startswith("-")), None
    )

    if name_filter:
        pattern = f"{name_filter}*.py"
    else:
        pattern = "test_*.py"

    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=TESTS_DIR, pattern=pattern)

    runner = unittest.TextTestRunner(verbosity=verbosity, buffer=True)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
