-- SLA mart: actual transit per shipment against the contractual allowance.
--
-- Standard service level terms apply unless a shipment-level override exists;
-- breaches are flagged and quantified in breach_hours.

select
    transit.shipment_id,
    transit.carrier,
    transit.transit_hours,
    terms.sla_hours as standard_sla_hours,
    cast(transit.transit_hours > terms.sla_hours as boolean) as sla_breached,
    cast(greatest(transit.transit_hours - terms.sla_hours, 0.0) as double) as breach_hours
from {{ ref('transit_duration') }} as transit
inner join {{ source('logistics_raw', 'sla_terms') }} as terms
    on transit.carrier = terms.carrier_code
    and terms.service_level = 'standard'
