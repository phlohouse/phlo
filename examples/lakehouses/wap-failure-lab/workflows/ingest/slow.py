"""Deliberately slow ingest asset for cancellation evidence.

``slow_sensor_feed`` runs a bounded tick loop (~15 minutes at one tick per
second) so acceptance runs can exercise a genuinely in-flight Dagster
cancellation: launch it, wait until ticks appear in the run log, then cancel.
The asset never reads inbound data and never touches the WAP contract — it
exists only to hold a run open long enough to terminate deterministically.

It is excluded from every schedule; operators materialize it on demand only.
"""

import time

import dagster as dg

TICKS = 900
TICK_SECONDS = 1.0


@dg.asset(
    name="slow_sensor_feed",
    group_name="ingest",
    description=(
        "Bounded idle feed used to exercise run cancellation. Ticks once per "
        "second for TICKS iterations; Dagster terminates it on cancel."
    ),
)
def slow_sensor_feed(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """Idle tick loop; cancellation interrupts the sleep between ticks."""
    for tick in range(TICKS):
        context.log.info("slow_sensor_feed tick %s/%s", tick + 1, TICKS)
        time.sleep(TICK_SECONDS)
    return dg.MaterializeResult(metadata={"ticks": TICKS})
