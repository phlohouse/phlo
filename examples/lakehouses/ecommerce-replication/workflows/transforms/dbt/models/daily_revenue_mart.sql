-- Daily revenue mart over the order lifecycle facts, partitioned by order date.
select
    ordered_date,
    currency,
    status,
    count(*) as order_count,
    sum(line_count) as line_count,
    round(sum(total_amount), 2) as gross_revenue,
    round(sum(lines_total), 2) as recognized_lines_total,
    round(sum(paid_total), 2) as collected_revenue
from {{ ref('order_lifecycle_facts') }}
group by ordered_date, currency, status
