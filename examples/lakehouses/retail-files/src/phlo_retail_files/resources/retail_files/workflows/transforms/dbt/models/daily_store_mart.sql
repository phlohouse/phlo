select
    partition_date as sales_date,
    store_id,
    count(*) as line_count,
    count(distinct transaction_id) as transaction_count,
    sum(gross_amount) as gross_amount,
    sum(discount_amount) as discount_amount,
    sum(tax_amount) as tax_amount,
    sum(net_amount) as net_amount
from {{ ref('sales_facts') }}
group by 1, 2
