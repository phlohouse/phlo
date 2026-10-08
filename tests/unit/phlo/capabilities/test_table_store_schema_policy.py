"""Advertisers must accept policy keywords; legacy contracts stay unchanged."""

from types import SimpleNamespace

import pytest

from phlo.capabilities import SchemaPolicyTableStore
from phlo.capabilities.interfaces import TableStore, TableStoreSupport
from phlo.capabilities.table_store import schema_policy_kwargs
from phlo.exceptions import PhloConfigError


@pytest.mark.parametrize("policy", ["strict", "additive", "drop_extra"])
@pytest.mark.parametrize(
    "method", ["ensure_table", "append_parquet", "merge_parquet", "overwrite_parquet"]
)
def test_advertiser_rejects_missing_policy_keyword(policy, method):
    provider = SimpleNamespace(
        support=TableStoreSupport(schema_policies=frozenset({policy})),
        **{method: lambda *, table_name: None},
    )
    with pytest.raises(PhloConfigError, match=f"{method} cannot accept schema_policy"):
        schema_policy_kwargs(provider, policy, methods=(method,))


def test_runtime_protocol_check_is_not_signature_conformance():
    class Advertiser(TableStore):
        @property
        def support(self):
            return TableStoreSupport(schema_policies=frozenset({"strict"}))

    provider = Advertiser()
    assert isinstance(provider, SchemaPolicyTableStore)
    with pytest.raises(PhloConfigError, match="ensure_table cannot accept schema_policy"):
        schema_policy_kwargs(provider, "strict", methods=("ensure_table",))


@pytest.mark.parametrize("default", ["strict", "additive"])
def test_legacy_provider_keeps_callers_default(default):
    assert (
        schema_policy_kwargs(object(), default, methods=("merge_parquet",), legacy_default=default)
        == {}
    )
    with pytest.raises(PhloConfigError, match="does not support explicit"):
        schema_policy_kwargs(
            object(), "drop_extra", methods=("merge_parquet",), legacy_default=default
        )


def test_unsupported_and_unknown_policies_fail_before_signature_lookup():
    provider = SimpleNamespace(support=TableStoreSupport(schema_policies=frozenset({"strict"})))
    with pytest.raises(PhloConfigError, match="does not support 'additive'"):
        schema_policy_kwargs(provider, "additive", methods=("merge_parquet",))
    with pytest.raises(PhloConfigError, match="Unknown schema policy"):
        schema_policy_kwargs(provider, "typo", methods=("merge_parquet",))
