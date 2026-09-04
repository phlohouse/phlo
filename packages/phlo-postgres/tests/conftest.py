"""Test configuration for phlo-postgres.

Puts the tests directory on ``sys.path`` so test modules can import the
shared non-collected helpers (``_dataset_test_backends``) by module name.
"""

import sys
from pathlib import Path

TESTS_DIR = str(Path(__file__).parent)
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)
