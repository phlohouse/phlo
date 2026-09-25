# phlo-transform

`phlo-transform` provides the SQL transform capability for Phlo. It exposes assets declared with `phlo.transform.sql` to installed orchestrator adapters.

## Install

```bash
pip install phlo-transform
```

## Declare a transform

```python
import phlo


@phlo.transform.sql(
    table="silver.orders",
    depends_on=["bronze.orders"],
    materialized="table",
)
def orders() -> str:
    return "select * from bronze.orders"
```

The package registers `TransformProvider` in `phlo.plugins.transformation_providers` and `TransformAssetProvider` in `phlo.plugins.assets`.
