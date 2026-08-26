select i.store_id, i.product_id, i.partition_date, i.on_hand, i.reserved, i.reorder_point, i.safety_stock,
       i.available_to_sell, i.available_to_sell <= i.reorder_point as reorder_required,
       i.available_to_sell = 0 as stockout
from {{ ref('inventory_balances') }} i
