"""Policy sync controller.

This module provides the control-plane component for synchronizing canonical
RBAC policies to backend-native enforcement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from phlo.logging import get_logger
from phlo.rbac.compiler import (
    COMPILER_REGISTRY,
    CompilerContext,
    GovernanceCompiler,
)
from phlo.rbac.config import RBACConfigLoader
from phlo.rbac.models import (
    CanonicalRBAC,
    SyncPlan,
    SyncResult,
    VerifyResult,
)

logger = get_logger(__name__)


class SyncController:
    """Controller for synchronizing canonical RBAC to backend-native enforcement."""

    def __init__(
        self,
        loader: RBACConfigLoader | None = None,
        compilers: dict[str, GovernanceCompiler] | None = None,
    ):
        """Initialize the sync controller.

        ``loader`` defaults to a loader with the default config path;
        ``compilers`` maps backend names to prebuilt compiler instances.
        """
        self._loader = loader or RBACConfigLoader()
        self._compilers = compilers or {}

    def load_rbac(self) -> CanonicalRBAC:
        """Load the canonical RBAC model."""
        return self._loader.load()

    def validate(self) -> tuple[bool, list[str]]:
        """Return (is_valid, error_messages) from the config loader."""
        return self._loader.validate()

    def plan(
        self,
        backends: list[str] | None = None,
        environment: str = "development",
    ) -> dict[str, SyncPlan]:
        """Compile sync plans for ``backends`` (default: all registered)
        under ``environment``; backends without a compiler are skipped with
        a warning."""
        rbac = self.load_rbac()
        plans: dict[str, SyncPlan] = {}

        target_backends = backends or list(COMPILER_REGISTRY.keys())

        for backend_name in target_backends:
            compiler = self._get_or_create_compiler(backend_name)
            if compiler is None:
                logger.warning("compiler_not_found", backend=backend_name)
                continue

            context = CompilerContext(
                environment=environment,
                backend_name=backend_name,
            )

            try:
                plan = compiler.plan(rbac, context)
                plans[backend_name] = plan
                logger.info(
                    "sync_plan_created",
                    backend=backend_name,
                    changes=len(plan.changes),
                )
            except Exception as e:
                logger.error(
                    "sync_plan_failed",
                    backend=backend_name,
                    error=str(e),
                )

        return plans

    def sync(
        self,
        backends: list[str] | None = None,
        environment: str = "development",
        dry_run: bool = False,
    ) -> dict[str, SyncResult]:
        """Plan and apply RBAC changes on ``backends`` (default: all
        registered) under ``environment``. With ``dry_run`` the plan is
        computed but nothing is applied. Compiler failures are logged and
        reported as failed results rather than raised."""
        rbac = self.load_rbac()
        results: dict[str, SyncResult] = {}

        target_backends = backends or list(COMPILER_REGISTRY.keys())

        for backend_name in target_backends:
            compiler = self._get_or_create_compiler(backend_name)
            if compiler is None:
                logger.warning("compiler_not_found", backend=backend_name)
                continue

            context = CompilerContext(
                environment=environment,
                backend_name=backend_name,
                dry_run=dry_run,
            )

            try:
                plan = compiler.plan(rbac, context)

                if dry_run:
                    results[backend_name] = SyncResult(
                        success=True,
                        backend=backend_name,
                        version_hash=plan.version_hash,
                        applied_count=0,
                        failed_count=0,
                        errors=(),
                        revert_ids=(),
                    )
                    continue

                success_ids, errors = compiler.apply(plan, context)

                results[backend_name] = SyncResult(
                    success=len(errors) == 0,
                    backend=backend_name,
                    version_hash=plan.version_hash,
                    applied_count=len(success_ids),
                    failed_count=len(errors),
                    errors=tuple(errors),
                    revert_ids=tuple(success_ids),
                )

                logger.info(
                    "sync_completed",
                    backend=backend_name,
                    success=results[backend_name].success,
                    applied=results[backend_name].applied_count,
                    failed=results[backend_name].failed_count,
                )

            except Exception as e:
                logger.error(
                    "sync_failed",
                    backend=backend_name,
                    error=str(e),
                )
                results[backend_name] = SyncResult(
                    success=False,
                    backend=backend_name,
                    version_hash=rbac.version_hash or "",
                    applied_count=0,
                    failed_count=1,
                    errors=(str(e),),
                )

        return results

    def verify(
        self,
        backends: list[str] | None = None,
        environment: str = "development",
    ) -> dict[str, VerifyResult]:
        """Compare backend state with the desired RBAC model on ``backends``
        (default: all registered) under ``environment``; backends whose
        verification raises are logged and omitted from the results."""
        rbac = self.load_rbac()
        results: dict[str, VerifyResult] = {}

        target_backends = backends or list(COMPILER_REGISTRY.keys())

        for backend_name in target_backends:
            compiler = self._get_or_create_compiler(backend_name)
            if compiler is None:
                logger.warning("compiler_not_found", backend=backend_name)
                continue

            context = CompilerContext(
                environment=environment,
                backend_name=backend_name,
            )

            try:
                result = compiler.verify(rbac, context)
                results[backend_name] = result

                logger.info(
                    "verify_completed",
                    backend=backend_name,
                    in_sync=result.in_sync,
                    missing=len(result.missing),
                    extra=len(result.extra),
                )

            except Exception as e:
                logger.error(
                    "verify_failed",
                    backend=backend_name,
                    error=str(e),
                )

        return results

    def revert(
        self,
        revert_ids: list[str],
        backends: list[str] | None = None,
        environment: str = "development",
    ) -> dict[str, tuple[list[str], list[str]]]:
        """Undo the given ``revert_ids`` on ``backends`` (default: all
        registered); per-backend failures are logged and returned as error
        lists rather than raised."""
        results: dict[str, tuple[list[str], list[str]]] = {}

        target_backends = backends or list(COMPILER_REGISTRY.keys())

        for backend_name in target_backends:
            compiler = self._get_or_create_compiler(backend_name)
            if compiler is None:
                continue

            context = CompilerContext(
                environment=environment,
                backend_name=backend_name,
            )

            try:
                success_ids, errors = compiler.revert(revert_ids, context)
                results[backend_name] = (success_ids, errors)

                logger.info(
                    "revert_completed",
                    backend=backend_name,
                    success=len(success_ids),
                    errors=len(errors),
                )

            except Exception as e:
                logger.error(
                    "revert_failed",
                    backend=backend_name,
                    error=str(e),
                )
                results[backend_name] = ([], [str(e)])

        return results

    def _get_or_create_compiler(
        self,
        backend_name: str,
    ) -> GovernanceCompiler | None:
        """Get or create a compiler for the specified backend.

        Looks up a registered governance backend from the capability registry
        and wires it to the compiler for actual enforcement.
        """
        if backend_name in self._compilers:
            return self._compilers[backend_name]

        from phlo.capabilities.registry import get_capability_registry
        from phlo.rbac.compiler import get_compiler

        registry = get_capability_registry()
        registered_backends = registry.list("governance_backend")

        backend_instance = None
        for spec in registered_backends:
            if spec.name == backend_name:
                backend_instance = spec.provider
                break

        compiler = get_compiler(backend_name, backend=backend_instance)
        if compiler:
            self._compilers[backend_name] = compiler

        return compiler

    def list_available_backends(self) -> list[str]:
        """List available backend names."""
        return list(COMPILER_REGISTRY.keys())


@dataclass
class SyncReport:
    """Structured sync report as defined in Spec 0017."""

    policy_version_hash: str
    backend: str
    environment: str
    planned_count: int
    applied_count: int
    verification_result: bool | None
    drift_summary: dict[str, int]
    request_id: str | None = None
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Convert report to dictionary."""
        return {
            "policy_version_hash": self.policy_version_hash,
            "backend": self.backend,
            "environment": self.environment,
            "planned_count": self.planned_count,
            "applied_count": self.applied_count,
            "verification_result": self.verification_result,
            "drift_summary": self.drift_summary,
            "request_id": self.request_id,
            "errors": list(self.errors),
        }
