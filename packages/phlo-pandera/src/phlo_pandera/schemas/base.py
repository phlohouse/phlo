"""PhloSchema base class with smart defaults.

This module provides the PhloSchema base class which extends Pandera's
DataFrameModel with standard phlo configuration. Using PhloSchema eliminates
the need to specify Config on every schema definition.

Default Configuration:
    - ``strict=False``: Allows extra columns (useful for DLT metadata like
      ``_dlt_id``, ``_dlt_load_id``)
    - ``coerce=True``: Automatically coerce types to match schema definitions

Important Notes:
    - For optional fields (e.g., ``str | None``), you must use ``Field(nullable=True)``.
      This is a Pandera requirement when ``coerce=True``.
    - The base class is designed to be extended, not instantiated directly.

Example:
    ```python
    from phlo_pandera.schemas import PhloSchema
    from pandera.pandas import Field

    class RawUserEvents(PhloSchema):
        '''Schema for raw user events with DLT metadata.'''
        id: str = Field(unique=True)
        type: str
        actor_login: str | None = Field(nullable=True)  # Required for nullable!
        created_at: str
        # No Config needed - defaults are applied automatically
        # Extra columns like _dlt_id, _dlt_load_id are allowed (strict=False)
    ```

See Also:
    - Pandera documentation for DataFrameModel configuration
    - ``phlo_pandera.schema_extractor``: Schema extraction utilities
    - ``phlo_pandera.checks_extra``: SchemaCheck for validation

"""

from __future__ import annotations

from pandera.pandas import DataFrameModel


class PhloSchema(DataFrameModel):
    """Base schema with phlo smart defaults.

    Extends Pandera DataFrameModel so subclasses get ``strict=False`` (extra DLT
    metadata columns like ``_dlt_id`` are allowed) and ``coerce=True`` (values
    are coerced to match the schema) without declaring Config each time.

    Note:
        For optional fields (e.g., ``str | None``), you must use ``Field(nullable=True)``.
        This is a Pandera requirement when ``coerce=True``.

    Example:
        Basic usage with automatic defaults:

        ```python
        from phlo_pandera.schemas import PhloSchema
        from pandera.pandas import Field

        class RawEvents(PhloSchema):
            event_id: str = Field(unique=True)
            event_type: str
            payload: str | None = Field(nullable=True)
            created_at: str
            # Extra columns like _dlt_id are automatically allowed
        ```
    """

    class Config:
        """Default Pandera model configuration for all derived Phlo schemas."""

        strict = False  # Allow extra columns (DLT metadata)
        coerce = True  # Auto-coerce types
