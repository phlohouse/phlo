"""Extract the figures each published note states.

This is the insight stage in Python: the note is evidence, the claim is an
insight with a locator. The extraction is deliberately strict. A line that
looks like a published figure but does not parse stops the build, and the
final count must match the number of notes, stations and pollutants. A
silently missed figure is worse than a failed run.

Replace this module (and ``analysis.py`` and the report template) when you
adapt the preset to another domain.
"""

from __future__ import annotations

import re
from typing import Any

from source_to_report.domain import POLLUTANTS, POLLUTANTS_BY_ID, STATION_NAMES, STATIONS

LINE = re.compile(r"^(?P<pollutant>PM2\.5|NO2) monthly mean: (?P<figures>.+)\.$")
ENTRY = re.compile(r"^(?P<station>[A-Z][a-z]+) (?P<value>\d+(?:\.\d+)?) (?P<unit>\S+)$")


class ExtractionError(ValueError):
    """A published figure could not be read, or the count did not add up."""


def _name_to_id() -> dict[str, str]:
    return {name: station_id for station_id, name in STATION_NAMES.items()}


def extract_claims(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one claim per published figure, with its note, line and sentence."""
    names = _name_to_id()
    claims: list[dict[str, Any]] = []

    for note in notes:
        for number, line in enumerate(str(note["body"]).splitlines(), start=1):
            match = LINE.match(line.strip())
            if match is None:
                looks_published = "monthly mean" in line.lower() and any(
                    line.startswith(f"{pollutant} ") for pollutant in POLLUTANTS_BY_ID
                )
                if looks_published:
                    raise ExtractionError(
                        f"{note['path']}:{number}: unrecognised published figure: {line!r}"
                    )
                continue

            pollutant = match.group("pollutant")
            unit = POLLUTANTS_BY_ID[pollutant].unit
            for part in (piece.strip() for piece in match.group("figures").split(",")):
                entry = ENTRY.match(part)
                if entry is None:
                    raise ExtractionError(
                        f"{note['path']}:{number}: cannot read figure {part!r} in {line!r}"
                    )
                station_name = entry.group("station")
                if station_name not in names:
                    raise ExtractionError(
                        f"{note['path']}:{number}: unknown station {station_name!r}"
                    )
                if entry.group("unit") != unit:
                    raise ExtractionError(
                        f"{note['path']}:{number}: {pollutant} is measured in {unit}, "
                        f"not {entry.group('unit')!r}"
                    )
                station_id = names[station_name]
                claims.append(
                    {
                        "claim_id": f"{note['month']}-{station_id}-{pollutant}",
                        "source_path": note["path"],
                        "line": number,
                        "month": note["month"],
                        "station_id": station_id,
                        "pollutant": pollutant,
                        "metric": "monthly_mean",
                        "value": float(entry.group("value")),
                        "unit": unit,
                        "sentence": line.strip(),
                    }
                )

    expected = len(notes) * len(STATIONS) * len(POLLUTANTS)
    if len(claims) != expected:
        raise ExtractionError(
            f"read {len(claims)} claims from {len(notes)} notes; expected {expected}"
        )
    ids = [claim["claim_id"] for claim in claims]
    if len(set(ids)) != len(ids):
        raise ExtractionError("two published figures produced the same claim id")
    return claims
