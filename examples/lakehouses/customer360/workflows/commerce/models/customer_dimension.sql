-- Type-2 customer dimension over commerce attributes keyed by canonical
-- identity. Every change stamped by updated_at opens a new version window;
-- windows are half-open [valid_from, valid_to) and never overlap, and exactly
-- one current_flag row exists per canonical_email.
with versions as (
    select
        canonical_email,
        customer_id,
        full_name,
        segment,
        region,
        updated_at as valid_from
    from {{ ref('stg_commerce_customers') }}
),

deduped as (
    select
        canonical_email,
        customer_id,
        full_name,
        segment,
        region,
        valid_from
    from (
        select
            v.*,
            row_number() over (
                partition by v.canonical_email, v.valid_from
                order by v.customer_id desc
            ) as pick
        from versions v
    ) ranked
    where ranked.pick = 1
),

sequenced as (
    select
        d.*,
        lead(valid_from) over (
            partition by d.canonical_email
            order by d.valid_from
        ) as next_valid_from
    from deduped d
)

select
    canonical_email,
    customer_id,
    full_name,
    segment,
    region,
    valid_from,
    coalesce(next_valid_from, timestamp '9999-12-31 00:00:00') as valid_to,
    case
        when next_valid_from is null then true
        else false
    end as current_flag
from sequenced
