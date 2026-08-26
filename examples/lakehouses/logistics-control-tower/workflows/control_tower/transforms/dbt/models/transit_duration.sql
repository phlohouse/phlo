-- Transit duration: pickup-to-delivery hours per delivered shipment.
--
-- Reads the unified carrier event stream and keeps only shipments whose
-- canonical state reached delivery. Bounded aggregations joined back, no
-- window frames.

with pickups as (

    select
        shipment_id,
        min(event_time) as first_pickup_at
    from {{ source('logistics_raw', 'carrier_events') }}
    where event_type = 'pickup'
    group by shipment_id

),

deliveries as (

    select
        shipment_id,
        max(event_time) as last_delivered_at
    from {{ source('logistics_raw', 'carrier_events') }}
    where event_type = 'delivered'
    group by shipment_id

),

state as (

    select
        shipment_id,
        carrier,
        canonical_state
    from {{ ref('canonical_shipment_state') }}

)

select
    state.shipment_id,
    state.carrier,
    state.canonical_state,
    pickups.first_pickup_at,
    deliveries.last_delivered_at,
    cast(date_diff('minute', pickups.first_pickup_at, deliveries.last_delivered_at) as double)
        / 60.0 as transit_hours
from state
inner join pickups
    on state.shipment_id = pickups.shipment_id
inner join deliveries
    on state.shipment_id = deliveries.shipment_id
where state.canonical_state = 'delivered'
