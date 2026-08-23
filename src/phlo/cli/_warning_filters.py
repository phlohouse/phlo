"""Startup warning filters for the Phlo CLI.

Importing this module suppresses known-benign third-party version-mismatch
warnings (urllib3/chardet/charset_normalizer from requests) so they never
reach CLI output. Import for side effect only.
"""

from __future__ import annotations

import warnings

warnings.filterwarnings(
    "ignore",
    message=r"urllib3 .* or chardet.*/charset_normalizer .* doesn't match a supported version!",
    module=r"requests(\..*)?$",
)
