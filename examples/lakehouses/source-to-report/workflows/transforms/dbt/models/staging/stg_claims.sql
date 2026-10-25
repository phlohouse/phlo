-- Published figures, typed. The line number is the locator back into the note.
{{ config(materialized='view') }}

select
    claim_id,
    source_path,
    cast(line as integer) as line,
    month,
    station_id,
    pollutant,
    metric,
    cast(value as double) as value,
    unit,
    sentence
from {{ source('raw', 'note_claims') }}
