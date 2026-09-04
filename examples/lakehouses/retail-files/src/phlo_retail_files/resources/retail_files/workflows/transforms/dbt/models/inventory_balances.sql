with ranked_snapshots as (
    select
        *,
        row_number() over (
            partition by inventory_snapshot_id
            order by _phlo_ingested_at desc, _phlo_run_id desc
        ) as snapshot_rank
    from {{ source('retail_raw', 'raw_inventory') }}
    where partition_date = '{{ var("partition_date_str") }}'
)

select
    inventory_snapshot_id,
    store_id,
    product_id,
    observed_at,
    on_hand,
    reserved,
    in_transit,
    reorder_point,
    safety_stock,
    on_hand - reserved as available_to_sell,
    partition_date
from ranked_snapshots
where snapshot_rank = 1
