"""Role setup through the authenticated, signed admin API, never direct storage writes."""

from __future__ import annotations

import hashlib
import ipaddress
import json
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import uuid4

import click
import httpx


@click.group("identity")
@click.option(
    "--api-url", required=True, help="Trusted deployment API URL, not a data environment."
)
@click.option("--token", envvar="PHLO_AUTHZ_TOKEN", hidden=True)
@click.pass_context
def identity_group(ctx: click.Context, api_url: str, token: str | None) -> None:
    """Inspect roles and assign members using an existing administrator session.

    Supply its access token via PHLO_AUTHZ_TOKEN. First administrators must use
    the deployment's existing authorized bootstrap, not this command.
    """
    url = urlsplit(api_url)
    loopback = url.hostname == "localhost"
    with suppress(ValueError):
        loopback = loopback or ipaddress.ip_address(url.hostname or "").is_loopback
    if (
        not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or (url.scheme != "https" and not (url.scheme == "http" and loopback))
    ):
        raise click.UsageError(
            "Use HTTPS, or HTTP on loopback, without credentials/query/fragment."
        )
    if not token:
        raise click.ClickException(
            "Set PHLO_AUTHZ_TOKEN to an existing authenticated access token."
        )
    ctx.obj = {"api_url": api_url.rstrip("/") + "/", "token": token}


@contextmanager
def _client(ctx: click.Context) -> Iterator[httpx.Client]:
    with httpx.Client(
        base_url=ctx.obj["api_url"],
        headers={"Authorization": f"Bearer {ctx.obj['token']}"},
        timeout=30,
        follow_redirects=False,
    ) as client:
        yield client


def _request(client: httpx.Client, method: str, path: str, **kwargs: Any) -> Any:
    try:
        response = client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        hints = {
            401: "Sign in again with this deployment's supported provider.",
            403: "Admin permission and, for signatures, recent verified MFA are required.",
            409: "Record or approval changed. Inspect members before starting a new operation.",
            422: "Check supported roles and request values.",
            503: "Identity storage or policy backend is unavailable. Restore it before retrying.",
        }
        raise click.ClickException(
            f"API returned {exc.response.status_code}. "
            + hints.get(exc.response.status_code, "Check the deployment API and audit logs.")
        ) from exc
    except (httpx.RequestError, ValueError) as exc:
        raise click.ClickException(
            "API response unavailable. Inspect current state; retry uncertain writes only with "
            "the same operation ID, expected version and exact payload."
        ) from exc


@identity_group.command("roles")
@click.pass_context
def roles(ctx: click.Context) -> None:
    """List supported assignments and candidate policy rules, not guaranteed grants."""
    with _client(ctx) as client:
        click.echo(json.dumps(_request(client, "GET", "api/v1/admin/roles"), indent=2))


@identity_group.command("members")
@click.pass_context
def members(ctx: click.Context) -> None:
    """List installation-wide managed assignments and current versions."""
    with _client(ctx) as client:
        click.echo(json.dumps(_request(client, "GET", "api/v1/admin/members"), indent=2))


@identity_group.command("permissions")
@click.argument("action")
@click.argument("resource_type")
@click.argument("resource_id")
@click.option(
    "--environment", default=None, help="Decision context, never authentication selection."
)
@click.pass_context
def permissions(
    ctx: click.Context, action: str, resource_type: str, resource_id: str, environment: str | None
) -> None:
    """Evaluate your session for a resource identity with no resource attributes.

    Operations still enforce their own full resource context and workflow checks.
    """
    params = {"action": action, "resource_type": resource_type, "resource_id": resource_id}
    if environment is not None:
        params["environment"] = environment
    with _client(ctx) as client:
        click.echo(
            json.dumps(_request(client, "GET", "api/v1/admin/permissions", params=params), indent=2)
        )


@identity_group.command("assign")
@click.argument("subject")
@click.option(
    "--role", "role_names", multiple=True, help="Complete replacement set; repeat for each role."
)
@click.option("--expected-version", type=click.IntRange(min=0), default=None)
@click.option("--justification", default=None)
@click.option(
    "--operation-id", default=None, help="Reuse only for an exact retry of this operation."
)
@click.option("--yes", is_flag=True, help="Confirm the displayed complete role replacement.")
@click.pass_context
def assign(
    ctx: click.Context,
    subject: str,
    role_names: tuple[str, ...],
    expected_version: int | None,
    justification: str | None,
    operation_id: str | None,
    yes: bool,
) -> None:
    """Review and sign a member's complete role set, preserving identity metadata.

    Omit --role for an interactive prompt defaulting to all existing roles.
    New subjects are user identities; invitation/service-account setup stays in Members.
    """
    with _client(ctx) as client:
        catalog = _request(client, "GET", "api/v1/admin/roles")
        supported = {item["name"] for item in catalog["items"]}
        current = next(
            (
                item
                for item in _request(client, "GET", "api/v1/admin/members")["items"]
                if item["subject"] == subject
            ),
            None,
        )
        if not role_names:
            if yes:
                raise click.UsageError("--yes requires an explicit complete --role set.")
            click.echo("Supported roles: " + ", ".join(sorted(supported)))
            role_names = tuple(
                click.prompt(
                    "Complete role set (comma separated)",
                    default=",".join(current["roles"]) if current else "viewer",
                ).split(",")
            )
        selected = sorted({role.strip() for role in role_names})
        if not selected or set(selected) - supported:
            raise click.UsageError("Choose only supported role names from the catalog.")
        version = (
            expected_version
            if expected_version is not None
            else current["version"]
            if current
            else 0
        )
        values = {
            "roles": selected,
            "active": current["active"] if current else True,
            "email": current["email"] if current else None,
            "principal_type": current["principal_type"] if current else "user",
        }
        operation_id = operation_id or str(uuid4())
        click.echo(
            json.dumps(
                {
                    "subject": subject,
                    "scope": "installation",
                    "expected_version": version,
                    "previous_roles": current["roles"] if current else [],
                    "replacement": values,
                    "operation_id": operation_id,
                },
                indent=2,
            )
        )
        if not yes:
            click.confirm("Sign and apply this complete assignment?", abort=True)
        if not justification:
            if yes:
                raise click.UsageError("--yes requires --justification.")
            justification = click.prompt("Justification")
        if not justification.strip():
            raise click.UsageError("A non-empty justification is required.")
        digest = hashlib.sha256(
            json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        signature = _request(
            client,
            "POST",
            "api/v1/signatures",
            json={
                "action": "admin.member.roles.change",
                "target_type": "member",
                "target_id": subject,
                "target_version": f"{version}:{digest}",
                "meaning": "approved",
                "justification": justification,
            },
            headers={"Idempotency-Key": f"signature:{operation_id}"},
        )
        result = _request(
            client,
            "PATCH",
            f"api/v1/admin/members/{quote(subject, safe='')}/roles",
            json={**values, "expected_version": version, "signature_id": signature["signature_id"]},
            headers={"Idempotency-Key": operation_id},
        )
        click.echo(json.dumps(result, indent=2))
