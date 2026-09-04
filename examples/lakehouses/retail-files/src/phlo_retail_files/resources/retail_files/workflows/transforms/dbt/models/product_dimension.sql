select product_id, product_name, category, subcategory, brand, supplier_id, unit_cost, list_price, active
from {{ source('retail_raw', 'raw_products') }}
