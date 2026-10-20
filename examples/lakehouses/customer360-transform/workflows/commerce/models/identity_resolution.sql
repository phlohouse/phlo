-- One mapping row per distinct address ever observed in any domain.
-- Case differences and plus-suffixes collapse onto one canonical_email so a
-- single person seen as alice.anderson@example.com, Alice.Anderson+legacy@...
-- and ALICE.ANDERSON+orders@... resolves to exactly one identity.
with domain_identities as (
    select
        observed_email,
        canonical_email,
        'commerce' as source_domain
    from {{ ref('stg_commerce_customers') }}

    union all

    select
        observed_email,
        canonical_email,
        'commerce' as source_domain
    from {{ ref('stg_commerce_orders') }}

    union all

    select
        observed_email,
        canonical_email,
        'support' as source_domain
    from {{ ref('stg_support_tickets') }}

    union all

    select
        observed_email,
        canonical_email,
        'marketing' as source_domain
    from {{ ref('stg_marketing_contacts') }}
),

distinct_identities as (
    select distinct
        observed_email,
        canonical_email,
        source_domain
    from domain_identities
)

-- One mapping row per observed address: when the same spelling appears in
-- several domains it resolves to the same canonical email, so the domain
-- column keeps the alphabetically-first origin for provenance.
select
    observed_email,
    min(canonical_email) as canonical_email,
    min(source_domain) as source_domain,
    min(observed_email) <> min(canonical_email) as is_variant
from distinct_identities
group by observed_email
