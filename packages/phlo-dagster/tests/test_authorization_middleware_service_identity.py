"""Tests for Dagster middleware service identity and correlation handling.

Covers principal extraction from verified OIDC tokens or regulated-mode
service tokens, fail-closed rejection of unsigned or forged identities,
mutation authorization carrying correlation IDs, least-privilege action
mapping for destructive GraphQL operations, and independent bulk-target
authorization that stops before the handler on denial.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from graphql import GraphQLError

from phlo.capabilities import AuthPrincipal
from phlo.security.adapters import EnforcementResult
from phlo.security.service_identity import (
    PHLO_CORRELATION_HEADER,
    create_service_token,
)
from phlo_dagster.authorization_middleware import DagsterGraphQLAuthorizationMiddleware
from phlo_dagster.authorization import resolve_graphql_operation
from _oidc_test_helpers import (
    AUDIENCE,
    ISSUER,
    JWKS_URL,
    JWKSResponse,
    key_and_jwks,
    token,
)


def _build_info(
    headers: dict[str, str],
    *,
    operation_name: str | None = "LaunchRun",
    field_name: str = "launchRun",
    parent_type_name: str = "Mutation",
    path_prev: object | None = None,
    operation_kind: str = "mutation",
    remote_addr: str = "127.0.0.1",
) -> SimpleNamespace:
    request = SimpleNamespace(
        headers=headers,
        remote_addr=remote_addr,
        path="/graphql",
        method="POST",
    )
    context = SimpleNamespace(request=request)
    operation = SimpleNamespace(
        name=SimpleNamespace(value=operation_name) if operation_name is not None else None,
        operation=SimpleNamespace(value=operation_kind),
    )
    parent_type = SimpleNamespace(name=parent_type_name)
    path = SimpleNamespace(prev=path_prev)
    return SimpleNamespace(
        context=context,
        operation=operation,
        field_name=field_name,
        parent_type=parent_type,
        path=path,
    )


def test_extract_principal_from_service_token(monkeypatch):
    monkeypatch.setenv("PHLO_SERVICE_SECRET", "test-secret")
    token = create_service_token("phlo-api")
    info = _build_info(
        {
            "Authorization": f"Bearer {token}",
        }
    )

    principal = DagsterGraphQLAuthorizationMiddleware()._extract_principal(info)

    assert principal is not None
    assert principal.subject == "service:phlo-api"
    assert principal.principal_type == "service"
    assert principal.attributes["authentication_source"] == "service_token"
    assert "initiating_principal" not in principal.attributes


def test_extract_principal_uses_authenticated_asgi_scope() -> None:
    principal = AuthPrincipal(subject="service:phlo-api", principal_type="service")
    info = _build_info({})
    request = info.context.request
    request.scope = {"phlo_authenticated_principal": principal}
    del info.context.request
    info.context._source = request

    assert DagsterGraphQLAuthorizationMiddleware()._extract_principal(info) is principal


def test_extract_principal_rejects_legacy_service_token_in_regulated_mode(monkeypatch):
    monkeypatch.setenv("PHLO_SERVICE_SECRET", "test-secret")
    monkeypatch.setenv("PHLO_ENVIRONMENT", "dev")
    monkeypatch.setenv("PHLO_REGULATED", "false")
    legacy_token = create_service_token("phlo-api")
    monkeypatch.setenv("PHLO_REGULATED", "true")

    principal = DagsterGraphQLAuthorizationMiddleware()._extract_principal(
        _build_info({"Authorization": f"Bearer {legacy_token}"})
    )

    assert principal is None


def test_extract_principal_does_not_authorize_on_unsigned_initiator_header(monkeypatch):
    monkeypatch.setenv("PHLO_SERVICE_SECRET", "test-secret")
    service_token = create_service_token("phlo-api")
    info = _build_info(
        {
            "Authorization": f"Bearer {service_token}",
            "X-Phlo-Initiator": "admin@example.com",
        }
    )

    principal = DagsterGraphQLAuthorizationMiddleware()._extract_principal(info)

    assert principal is not None
    assert "initiating_principal" not in principal.attributes


def test_extract_principal_rejects_unsigned_or_invalid_bearer(monkeypatch):
    monkeypatch.setenv("PHLO_SERVICE_SECRET", "test-secret")

    for value in ("arbitrary-user-token", "phlo-api:malformed"):
        info = _build_info({"Authorization": f"Bearer {value}"})
        assert DagsterGraphQLAuthorizationMiddleware()._extract_principal(info) is None


def test_extract_principal_rejects_expired_service_token(monkeypatch):
    monkeypatch.setenv("PHLO_SERVICE_SECRET", "test-secret")
    timestamp = str(int(time.time()) - 1000)
    message = f"phlo-api:{timestamp}:expired-nonce"
    signature = hmac.new(b"test-secret", message.encode(), hashlib.sha256).hexdigest()
    token = f"{message}:{signature}"

    assert (
        DagsterGraphQLAuthorizationMiddleware()._extract_principal(
            _build_info({"Authorization": f"Bearer {token}"})
        )
        is None
    )


def test_extract_principal_rejects_wrong_audience_and_unsigned_headers(monkeypatch):
    monkeypatch.setenv("PHLO_SERVICE_SECRET", "test-secret")
    wrong_audience = create_service_token("untrusted-service")
    middleware = DagsterGraphQLAuthorizationMiddleware()

    assert (
        middleware._extract_principal(_build_info({"Authorization": f"Bearer {wrong_audience}"}))
        is None
    )
    assert middleware._extract_principal(_build_info({"X-Dagster-User": "forged-user"})) is None
    assert (
        middleware._extract_principal(_build_info({"X-Dagster-Api-Token": "forged-token"})) is None
    )


def test_extract_principal_accepts_only_verified_oidc_access_token(monkeypatch):
    private_key, jwks = key_and_jwks()
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_JWKS_URL", JWKS_URL)
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_ALLOW_INSECURE_HTTP", "true")
    monkeypatch.setattr(
        "phlo_dagster.oidc_identity.httpx.stream",
        lambda *_args, **_kwargs: JWKSResponse(jwks),
    )

    principal = DagsterGraphQLAuthorizationMiddleware()._extract_principal(
        _build_info(
            {"X-Auth-Request-Access-Token": token(private_key, groups=["viewer", "analyst"])}
        )
    )

    assert principal is not None
    assert principal.subject == "viewer@example.com"
    assert principal.groups == ("viewer", "analyst")
    assert principal.attributes["authentication_source"] == "oidc"
    direct = DagsterGraphQLAuthorizationMiddleware()._extract_principal(
        _build_info({"Authorization": f"Bearer {token(private_key)}"})
    )
    assert direct is not None
    assert direct.subject == "viewer@example.com"


def test_extract_principal_rejects_invalid_oidc_claims_and_forged_proxy_headers(monkeypatch):
    private_key, jwks = key_and_jwks()
    another_key, _ = key_and_jwks()
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_JWKS_URL", JWKS_URL)
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_ALLOW_INSECURE_HTTP", "true")
    monkeypatch.setattr(
        "phlo_dagster.oidc_identity.httpx.stream",
        lambda *_args, **_kwargs: JWKSResponse(jwks),
    )
    middleware = DagsterGraphQLAuthorizationMiddleware()

    invalid_tokens = (
        token(another_key),
        token(private_key, issuer="https://wrong.example/"),
        token(private_key, audience="wrong-audience"),
        token(private_key, expires_in=-1000),
        token(private_key, not_before=int(time.time()) + 1000),
    )
    for invalid in invalid_tokens:
        assert (
            middleware._extract_principal(_build_info({"X-Auth-Request-Access-Token": invalid}))
            is None
        )

    assert (
        middleware._extract_principal(
            _build_info(
                {
                    "X-Auth-Request-User": "forged@example.com",
                    "X-Auth-Request-Groups": "admin",
                }
            )
        )
        is None
    )


@patch("phlo_dagster.authorization_middleware.enforce")
def test_verified_oidc_viewer_reaches_http_graphql_authorization(mock_enforce, monkeypatch):
    private_key, jwks = key_and_jwks()
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_JWKS_URL", JWKS_URL)
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_ALLOW_INSECURE_HTTP", "true")
    monkeypatch.setattr(
        "phlo_dagster.oidc_identity.httpx.stream",
        lambda *_args, **_kwargs: JWKSResponse(jwks),
    )
    mock_enforce.return_value = MagicMock(allowed=True, reason_code=None)
    info = _build_info(
        {"X-Auth-Request-Access-Token": token(private_key)},
        field_name="runsOrError",
        parent_type_name="Query",
        operation_kind="query",
    )

    assert (
        DagsterGraphQLAuthorizationMiddleware().resolve(lambda *_args, **_kwargs: "ok", None, info)
        == "ok"
    )
    assert mock_enforce.call_args.kwargs["action"] == "run.read"


def test_mandatory_mode_cannot_be_disabled():
    import pytest

    with pytest.raises(ValueError, match="mandatory"):
        DagsterGraphQLAuthorizationMiddleware(strict_mode=False)


def test_create_decision_context_prefers_correlation_header():
    info = _build_info({PHLO_CORRELATION_HEADER: "corr-123"})

    context = DagsterGraphQLAuthorizationMiddleware()._create_decision_context(info)

    assert context.request_id == "corr-123"


@patch("phlo_dagster.authorization_middleware.enforce")
def test_authorize_mutation_passes_correlation_id(mock_enforce, monkeypatch):
    monkeypatch.setenv("PHLO_SERVICE_SECRET", "test-secret")
    token = create_service_token("phlo-api")
    info = _build_info(
        {
            "Authorization": f"Bearer {token}",
            PHLO_CORRELATION_HEADER: "corr-456",
        },
        field_name="logTelemetry",
        operation_kind="mutation",
    )
    mock_enforce.return_value = MagicMock(allowed=True, reason_code=None)

    DagsterGraphQLAuthorizationMiddleware()._authorize_mutation(info, {})

    kwargs = mock_enforce.call_args.kwargs
    assert kwargs["request_id"] == "corr-456"
    assert kwargs["correlation_id"] == "corr-456"


@patch("phlo_dagster.authorization_middleware.enforce")
def test_authorize_mutation_uses_field_name_for_action_and_resource(mock_enforce, monkeypatch):
    monkeypatch.setenv("PHLO_SERVICE_SECRET", "test-secret")
    token = create_service_token("phlo-api")
    info = _build_info(
        {"Authorization": f"Bearer {token}"},
        operation_name=None,
        field_name="terminateRun",
    )
    mock_enforce.return_value = MagicMock(allowed=True, reason_code=None)

    DagsterGraphQLAuthorizationMiddleware()._authorize_mutation(info, {"runId": "run-42"})

    kwargs = mock_enforce.call_args.kwargs
    assert kwargs["action"] == "run.manage"
    assert kwargs["resource"].resource_type == "run"
    assert kwargs["resource"].resource_id == "runId=run-42"


@patch("phlo_dagster.authorization_middleware.enforce")
def test_graphql_conflicting_same_key_values_fail_closed(mock_enforce, monkeypatch):
    monkeypatch.setenv("PHLO_SERVICE_SECRET", "test-secret")
    service_token = create_service_token("phlo-api")
    info = _build_info(
        {"Authorization": f"Bearer {service_token}"},
        field_name="launchRun",
        operation_kind="mutation",
    )

    with pytest.raises(GraphQLError, match="Ambiguous GraphQL resource identity"):
        DagsterGraphQLAuthorizationMiddleware()._authorize_field(
            info,
            {
                "executionParams": {
                    "jobName": "job-a",
                    "repositoryName": "repo",
                    "repositoryLocationName": "loc",
                },
                "reexecutionParams": {
                    "jobName": "job-b",
                    "repositoryName": "repo",
                    "repositoryLocationName": "loc",
                },
            },
        )

    mock_enforce.assert_not_called()


@pytest.mark.parametrize(
    ("field", "action", "kwargs"),
    [
        (
            "launchRun",
            "run.execute",
            {
                "executionParams": {
                    "jobName": "orders",
                    "repositoryName": "repo",
                    "repositoryLocationName": "loc",
                }
            },
        ),
        ("terminateRun", "run.manage", {"runId": "run-42"}),
        ("deleteRun", "run.manage", {"runId": "run-42"}),
        ("freeConcurrencySlots", "run.manage", {"runId": "run-42"}),
        ("wipeAssets", "asset.manage", {"assetKey": {"path": ["orders"]}}),
        ("logTelemetry", "admin.manage", {}),
        ("setAutoMaterializePaused", "admin.manage", {}),
        ("stopRunningSchedule", "run.manage", {"id": "schedule-1"}),
    ],
)
@patch("phlo_dagster.authorization_middleware.enforce")
def test_destructive_graphql_operations_use_least_privilege(
    mock_enforce, monkeypatch, field: str, action: str, kwargs: dict[str, object]
) -> None:
    monkeypatch.setenv("PHLO_SERVICE_SECRET", "test-secret")
    token = create_service_token("phlo-api")
    info = _build_info(
        {"Authorization": f"Bearer {token}"},
        field_name=field,
        operation_kind="mutation",
    )

    def decision(**call_kwargs):  # noqa: ANN001
        return (
            EnforcementResult.allow()
            if call_kwargs["action"] == action
            else EnforcementResult.deny(reason_code="default_deny")
        )

    mock_enforce.side_effect = decision
    result = DagsterGraphQLAuthorizationMiddleware()._authorize_field(info, kwargs)

    assert result.allowed
    assert mock_enforce.call_args.kwargs["action"] == action


def test_launch_permission_cannot_authorize_destructive_run_mutations(monkeypatch):
    monkeypatch.setenv("PHLO_SERVICE_SECRET", "test-secret")
    token = create_service_token("phlo-api")
    middleware = DagsterGraphQLAuthorizationMiddleware()

    with patch(
        "phlo_dagster.authorization_middleware.enforce",
        side_effect=lambda **kwargs: (
            EnforcementResult.allow()
            if kwargs["action"] == "run.execute"
            else EnforcementResult.deny(reason_code="default_deny")
        ),
    ):
        launch = middleware._authorize_field(
            _build_info(
                {"Authorization": f"Bearer {token}"},
                field_name="launchRun",
                operation_kind="mutation",
            ),
            {
                "executionParams": {
                    "jobName": "orders",
                    "repositoryName": "repo",
                    "repositoryLocationName": "loc",
                }
            },
        )
        terminate = middleware._authorize_field(
            _build_info(
                {"Authorization": f"Bearer {token}"},
                field_name="terminateRun",
                operation_kind="mutation",
            ),
            {"runId": "run-42"},
        )

    assert launch.allowed
    assert not terminate.allowed


def test_graphql_operation_registry_returns_exact_destructive_actions() -> None:
    assert resolve_graphql_operation("mutation", "launchRun").action == "run.execute"
    assert resolve_graphql_operation("mutation", "terminateRun").action == "run.manage"
    assert resolve_graphql_operation("mutation", "deleteRun").action == "run.manage"
    assert resolve_graphql_operation("mutation", "freeConcurrencySlots").action == "run.manage"
    assert resolve_graphql_operation("mutation", "setAutoMaterializePaused").resource_keys == ()
    assert resolve_graphql_operation("mutation", "stopRunningSchedule").resource_keys == (
        "id",
        "scheduleOriginId",
        "scheduleSelectorId",
    )


def test_graphql_destructive_operation_requires_live_resource_argument(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_SERVICE_SECRET", "test-secret")
    token = create_service_token("phlo-api")
    middleware = DagsterGraphQLAuthorizationMiddleware()

    with pytest.raises(Exception, match="authoritative resource"):
        middleware._authorize_field(
            _build_info(
                {"Authorization": f"Bearer {token}"},
                field_name="stopRunningSchedule",
                operation_kind="mutation",
            ),
            {},
        )


@patch("phlo_dagster.authorization_middleware.enforce")
def test_bulk_graphql_targets_are_authorized_independently(mock_enforce, monkeypatch) -> None:
    monkeypatch.setenv("PHLO_SERVICE_SECRET", "test-secret")
    service_token = create_service_token("phlo-api")
    mock_enforce.return_value = EnforcementResult.allow()
    middleware = DagsterGraphQLAuthorizationMiddleware()

    result = middleware._authorize_field(
        _build_info(
            {"Authorization": f"Bearer {service_token}"},
            field_name="terminateRuns",
            operation_kind="mutation",
        ),
        {"runIds": ["run-1", "run-2"]},
    )

    assert result.allowed
    resources = [call.kwargs["resource"].resource_id for call in mock_enforce.call_args_list]
    assert resources == ["runId=run-1", "runId=run-2"]


@patch("phlo_dagster.authorization_middleware.enforce")
def test_bulk_graphql_denial_stops_before_handler(mock_enforce, monkeypatch) -> None:
    monkeypatch.setenv("PHLO_SERVICE_SECRET", "test-secret")
    service_token = create_service_token("phlo-api")
    mock_enforce.side_effect = [
        EnforcementResult.allow(),
        EnforcementResult.deny(reason_code="default_deny"),
    ]
    middleware = DagsterGraphQLAuthorizationMiddleware()

    result = middleware._authorize_field(
        _build_info(
            {"Authorization": f"Bearer {service_token}"},
            field_name="terminateRuns",
            operation_kind="mutation",
        ),
        {"runIds": ["run-1", "run-2"]},
    )

    assert not result.allowed
    assert mock_enforce.call_count == 2


def test_map_operation_to_action_rejects_unclassified_fields() -> None:
    middleware = DagsterGraphQLAuthorizationMiddleware()

    import pytest

    with pytest.raises(RuntimeError, match="Unclassified"):
        middleware._map_operation_to_action("customMutation")
    with pytest.raises(Exception, match="Unclassified"):
        middleware._map_operation_to_action(None)


def test_get_selection_resource_rejects_unclassified_fields() -> None:
    middleware = DagsterGraphQLAuthorizationMiddleware()

    import pytest

    with pytest.raises(RuntimeError, match="Unclassified"):
        middleware._get_selection_resource("customMutation")
    with pytest.raises(Exception, match="Unclassified"):
        middleware._get_selection_resource(None)


@patch("phlo_dagster.authorization_middleware.is_regulated", return_value=True)
@patch("phlo_dagster.authorization_middleware.enforce")
def test_resolve_skips_nested_mutation_fields(mock_enforce, _mock_regulated):
    middleware = DagsterGraphQLAuthorizationMiddleware()
    next_fn = MagicMock(return_value="ok")
    info = _build_info(
        {}, field_name="run", parent_type_name="LaunchRunSuccess", path_prev=object()
    )

    result = middleware.resolve(next_fn, None, info)

    assert result == "ok"
    mock_enforce.assert_not_called()
