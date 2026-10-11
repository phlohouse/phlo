"""Registry of governance backend compilers.

Owns the backend-name to compiler-class mapping. It sits above both
``phlo.rbac.compiler`` (base classes and the Trino compiler) and
``phlo.rbac.compilers`` (the other blessed backends), so neither module has
to import the other in both directions.
"""

from __future__ import annotations

from phlo.capabilities.interfaces import GovernanceBackend
from phlo.rbac.compiler import GovernanceCompiler, TrinoCompiler
from phlo.rbac.compilers import MinioCompiler, NessieCompiler, PostgresCompiler

COMPILER_REGISTRY: dict[str, type[GovernanceCompiler]] = {
    "trino": TrinoCompiler,
    "postgres": PostgresCompiler,
    "minio": MinioCompiler,
    "nessie": NessieCompiler,
}


def get_compiler(
    backend_name: str,
    backend: GovernanceBackend | None = None,
) -> GovernanceCompiler | None:
    """Return a compiler instance for backend_name, or None when unregistered."""
    compiler_class = COMPILER_REGISTRY.get(backend_name)
    if compiler_class is None:
        return None
    return compiler_class(backend=backend)
