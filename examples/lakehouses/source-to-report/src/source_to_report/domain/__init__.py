"""The reference domain: a small air-quality monitoring network.

This module is the domain pack's vocabulary. Along with ``extraction.py``,
``analysis.py``, ``report.html.j2``, ``workflows/`` and ``data/``, it is the
part of the preset you replace for a new domain. Everything else in
``source_to_report`` is the generic spine and should not need to know what a
pollutant is.

The fixture data is synthetic and deterministic. It is not a real monitoring
network.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

YEAR = 2025
PERIOD_START = date(YEAR, 1, 1)
PERIOD_END = date(YEAR, 12, 31)

TITLE = "Air quality brief"
SUBTITLE = "Reference monitoring network"
DESCRIPTION = (
    "Hourly measurements from three reference stations, checked against the "
    "figures the network publishes each month."
)

# A published figure is treated as reconciled when it is within this distance
# of the measurement it describes, in the pollutant's unit. The dbt project
# carries the same value; a test keeps the two in step.
CLAIM_TOLERANCE = 0.05


@dataclass(frozen=True, slots=True)
class Station:
    """A monitoring station in the reference network."""

    id: str
    name: str
    kind: str


@dataclass(frozen=True, slots=True)
class Pollutant:
    """A pollutant, its unit, and the objectives it is judged against."""

    id: str
    slug: str
    unit: str
    annual_objective: float
    short_term_objective: float
    short_term_period: str


STATIONS = (
    Station(id="riverside", name="Riverside", kind="urban background"),
    Station(id="harbour", name="Harbour", kind="coastal background"),
    Station(id="northgate", name="Northgate", kind="industrial perimeter"),
)

POLLUTANTS = (
    Pollutant(
        id="PM2.5",
        slug="pm25",
        unit="µg/m³",
        annual_objective=10.0,
        short_term_objective=15.0,
        short_term_period="24-hour mean",
    ),
    Pollutant(
        id="NO2",
        slug="no2",
        unit="µg/m³",
        annual_objective=40.0,
        short_term_objective=200.0,
        short_term_period="1-hour mean",
    ),
)

STATION_IDS = tuple(station.id for station in STATIONS)
STATION_NAMES = {station.id: station.name for station in STATIONS}
POLLUTANTS_BY_ID = {pollutant.id: pollutant for pollutant in POLLUTANTS}
POLLUTANT_IDS = tuple(pollutant.id for pollutant in POLLUTANTS)
