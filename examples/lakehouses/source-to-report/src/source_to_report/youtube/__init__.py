"""The YouTube channel pack.

The spine is the same one the air-quality pack uses: the pack supplies a
reader, a contract, dbt models and a page. Only this module and its template
know what a video, a view or an engagement rate is.
"""

from __future__ import annotations

from datetime import date

TITLE = "Channel brief"
DESCRIPTION = "What one channel published in 2025, and how the audience responded."
PERIOD_START = date(2025, 1, 1)
PERIOD_END = date(2025, 12, 31)
FORMATS = ("tutorial", "interview", "demo")
