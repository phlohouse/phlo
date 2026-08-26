"""Weather-station coverage gate.

Every observation's ``station_id`` must resolve to a place in the civic
registry: an orphan station silently vanishes from the places join and would
undercount regional rollups downstream. The known-station set is imported
from the fixture generator so the gate and the fixtures cannot drift apart.
"""

from __future__ import annotations

import pandas as pd

from scripts.generate_fixtures import WEATHER_STATIONS

KNOWN_STATIONS = frozenset(WEATHER_STATIONS)


def assert_known_stations(observations: object) -> str | None:
    """Gate: every observation station must exist in the civic registry."""
    frame = observations if isinstance(observations, pd.DataFrame) else pd.DataFrame(observations)
    if frame.empty or "station_id" not in frame.columns:
        return None
    unknown = sorted(set(frame["station_id"]).difference(KNOWN_STATIONS))
    if unknown:
        offenders = frame[frame["station_id"].isin(unknown)]["observed_at"].head(5).tolist()
        return f"observations reference unknown stations {unknown}: {offenders}"
    return None
