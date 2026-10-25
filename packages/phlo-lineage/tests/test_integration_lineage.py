"""Import-surface checks for phlo-lineage's public hook types.

Behavioural coverage lives in ``test_lineage_store.py`` and
``test_column_lineage.py``; this module only pins the public names.
"""


def test_lineage_event_context_importable():
    """Test LineageEventContext is importable."""
    from phlo.hooks import LineageEventContext

    assert LineageEventContext is not None


def test_lineage_event_emitter_importable():
    """Test LineageEventEmitter is importable."""
    from phlo.hooks import LineageEventEmitter

    assert LineageEventEmitter is not None
