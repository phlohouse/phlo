"""Makes sibling test helper modules importable.

Inserts the tests directory at the front of ``sys.path`` before collection so
local fixture modules resolve without installation.
"""

from __future__ import annotations

import sys
from pathlib import Path

TEST_DIR = str(Path(__file__).parent)
if TEST_DIR not in sys.path:
    sys.path.insert(0, TEST_DIR)
