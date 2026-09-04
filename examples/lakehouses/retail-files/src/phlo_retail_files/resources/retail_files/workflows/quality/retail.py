from __future__ import annotations

import pandas as pd

from workflows.schemas.retail import InventorySchema, ProductsSchema, SalesSchema


def validate_retail(
    sales: pd.DataFrame,
    products: pd.DataFrame,
    stores: pd.DataFrame,
    promotions: pd.DataFrame,
    inventory: pd.DataFrame,
) -> None:
    SalesSchema.validate(sales)
    ProductsSchema.validate(products)
    InventorySchema.validate(inventory)
    if sales.duplicated(["transaction_id", "line_id"]).any():
        raise ValueError("Duplicate transaction/line key")
    if (
        not set(sales.currency).issubset({"USD"})
        or not set(sales.channel).issubset({"store", "online"})
        or not set(sales.payment_method).issubset({"card", "cash", "wallet"})
    ):
        raise ValueError("Invalid accepted value")
    if (sales.is_return != (sales.quantity < 0)).any():
        raise ValueError("Return indicator must match quantity sign")
    expected = (sales.gross_amount - sales.discount_amount + sales.tax_amount).round(2)
    if not expected.equals(sales.net_amount.round(2)):
        raise ValueError("Transaction arithmetic reconciliation failed")
    for column, values, label in [
        ("product_id", set(products.product_id), "product"),
        ("store_id", set(stores.store_id), "store"),
        ("promotion_id", set(promotions.promotion_id) | {None}, "promotion"),
    ]:
        if not set(sales[column].dropna()).issubset(values):
            raise ValueError(f"Unknown {label} reference")
    if not set(inventory.product_id).issubset(set(products.product_id)) or not set(
        inventory.store_id
    ).issubset(set(stores.store_id)):
        raise ValueError("Unknown inventory reference")
    if (inventory.reserved > inventory.on_hand).any() or (
        inventory.safety_stock > inventory.reorder_point
    ).any():
        raise ValueError("Inventory business constraint failed")
