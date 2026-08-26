select f.partition_date, p.category, p.brand, count(*) as line_count,
       sum(f.quantity) as units, sum(f.net_amount) as net_sales
from {{ ref('sales_facts') }} f join {{ ref('product_dimension') }} p using (product_id)
group by 1, 2, 3
