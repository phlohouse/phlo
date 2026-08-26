select
    line_id,
    transaction_id,
    store_id,
    product_id,
    cast(sold_at as timestamp) as sold_at,
    quantity,
    unit_price,
    gross_amount, discount_amount, tax_amount, net_amount,
    partition_date
from {{ source('retail_raw', 'raw_sales') }}
