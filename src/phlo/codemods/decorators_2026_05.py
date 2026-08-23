"""May 2026 codemod for migrating legacy Phlo decorators.

Rewrites imports of ``phlo_ingestion`` (from ``phlo_dlt``/``phlo_dlt.decorator``)
and ``phlo_quality`` (from ``phlo.quality``), plus their decorator usages, into
attribute references on the top-level ``phlo`` package (``phlo.ingest.dlt`` and
``phlo.quality.pandera``), inserting ``import phlo`` when the module lacks one.

The rewrite runs on libcst so untouched code keeps its exact formatting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Decorators202605Migration:
    """Result of migrating one Python source string."""

    code: str
    changed: bool


def migrate_decorators_2026_05_source(source: str) -> Decorators202605Migration:
    """Migrate legacy Phlo decorators to the May 2026 API style."""
    cst = _load_libcst()
    module = cst.parse_module(source)
    migrated = module.visit(_decorators_202605_transformer(cst))
    code = migrated.code
    return Decorators202605Migration(code=code, changed=code != source)


def _load_libcst() -> Any:
    # libcst is an optional dependency (the codemods extra). Every helper
    # imports it lazily through here so importing phlo.codemods itself never
    # requires it, and a missing install surfaces as an actionable error.
    try:
        import libcst as cst
    except ImportError as exc:
        raise RuntimeError(
            "Decorator codemods require the codemods extra: install phlo[codemods]."
        ) from exc
    return cst


def _decorators_202605_transformer(cst: Any) -> Any:
    class Decorators202605Transformer(cst.CSTTransformer):
        """Rewrite legacy Phlo decorator references.

        Works in two phases: visit_Import/visit_ImportFrom first record every
        local name bound to phlo_ingestion/phlo_quality, then leave_* rewrites
        decorators using those bindings. leave_Module runs last, so it can
        decide from the accumulated state whether ``import phlo`` is needed.
        """

        def __init__(self) -> None:
            self.has_import_phlo = False
            self.needs_import_phlo = False
            self.ingestion_names: set[str] = {"phlo_ingestion"}
            self.quality_names: set[str] = {"phlo_quality"}

        def visit_Import(self, node: Any) -> bool:
            """Record an existing ``import phlo`` binding."""
            for alias in node.names:
                if _local_name(alias) == "phlo":
                    self.has_import_phlo = True
            return True

        def visit_ImportFrom(self, node: Any) -> bool:
            """Record import bindings for phlo, phlo_ingestion, and phlo_quality."""
            module_name = _module_name(node.module)
            if module_name == "phlo":
                for alias in _aliases(node.names):
                    if _local_name(alias) == "phlo":
                        self.has_import_phlo = True

            if module_name in {"phlo_dlt", "phlo_dlt.decorator"}:
                for alias in _aliases(node.names):
                    if _name_value(alias.name) == "phlo_ingestion":
                        self.ingestion_names.add(_local_name(alias))
                        self.needs_import_phlo = True

            if module_name == "phlo.quality":
                for alias in _aliases(node.names):
                    if _name_value(alias.name) == "phlo_quality":
                        self.quality_names.add(_local_name(alias))
                        self.needs_import_phlo = True

            return True

        def leave_ImportFrom(self, original_node: Any, updated_node: Any) -> Any:
            """Strip migrated ingestion and quality names from their original imports."""
            module_name = _module_name(original_node.module)
            names = updated_node.names
            if not isinstance(names, tuple):
                return updated_node

            if module_name in {"phlo_dlt", "phlo_dlt.decorator"}:
                filtered = [alias for alias in names if _name_value(alias.name) != "phlo_ingestion"]
                return _replace_import_aliases(updated_node, filtered)

            if module_name == "phlo.quality":
                filtered = [alias for alias in names if _name_value(alias.name) != "phlo_quality"]
                return _replace_import_aliases(updated_node, filtered)

            return updated_node

        def leave_Decorator(self, original_node: Any, updated_node: Any) -> Any:
            """Rewrite decorator expressions that target migrated entry points."""
            return updated_node.with_changes(
                decorator=self._migrate_decorator_expression(updated_node.decorator)
            )

        def leave_Module(self, original_node: Any, updated_node: Any) -> Any:
            """Insert ``import phlo`` when the walk rewrote a decorator and no binding exists."""
            # Runs after the whole module is walked, so needs_import_phlo
            # already covers every rewritten decorator and has_import_phlo is
            # final. A visit-time check could not know either yet.
            if not self.needs_import_phlo or self.has_import_phlo:
                return updated_node

            import_phlo = cst.SimpleStatementLine(
                body=[cst.Import(names=[cst.ImportAlias(name=cst.Name("phlo"))])]
            )
            body = list(updated_node.body)
            insert_at = _import_insert_index(body)
            body.insert(insert_at, import_phlo)
            return updated_node.with_changes(body=body)

        def _migrate_decorator_expression(self, expression: Any) -> Any:
            if isinstance(expression, cst.Call):
                return expression.with_changes(func=self._migrate_decorator_target(expression.func))
            return self._migrate_decorator_target(expression)

        def _migrate_decorator_target(self, expression: Any) -> Any:
            # Match on the local bindings recorded during the visit pass, so
            # aliased imports migrate too. Flagging needs_import_phlo from a
            # leave hook is safe because leave_Module runs last.
            if isinstance(expression, cst.Name):
                if expression.value in self.ingestion_names:
                    self.needs_import_phlo = True
                    return cst.parse_expression("phlo.ingest.dlt")
                if expression.value in self.quality_names:
                    self.needs_import_phlo = True
                    return cst.parse_expression("phlo.quality.pandera")

            if _matches_dotted_name(expression, ("phlo", "ingestion")):
                self.needs_import_phlo = True
                return cst.parse_expression("phlo.ingest.dlt")

            return expression

    return Decorators202605Transformer()


def _aliases(names: Any) -> tuple[Any, ...]:
    cst = _load_libcst()
    # A star import binds no explicit local names, so there is nothing to
    # record for the alias-matching phase.
    if isinstance(names, cst.ImportStar):
        return ()
    return names


def _local_name(alias: Any) -> str:
    if alias.asname is None:
        return _name_value(alias.name)
    return alias.asname.name.value


def _module_name(node: Any | None) -> str | None:
    cst = _load_libcst()
    if node is None:
        return None
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        base = _module_name(node.value)
        if base is None:
            return node.attr.value
        return f"{base}.{node.attr.value}"
    return None


def _name_value(node: Any) -> str:
    cst = _load_libcst()
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        return _module_name(node) or ""
    return ""


def _matches_dotted_name(expression: Any, parts: tuple[str, ...]) -> bool:
    return _module_name(expression) == ".".join(parts)


def _replace_import_aliases(
    import_from: Any,
    aliases: list[Any],
) -> Any:
    cst = _load_libcst()
    # Dropping the final alias removes the whole statement: an import with no
    # names would be invalid syntax.
    if not aliases:
        return cst.RemoveFromParent()
    return import_from.with_changes(names=tuple(aliases))


def _import_insert_index(body: list[Any]) -> int:
    # Python fixes the position of the module docstring and __future__ imports;
    # the new import must land after both.
    index = 0
    if body and _is_module_docstring(body[0]):
        index = 1

    while index < len(body) and _is_future_import(body[index]):
        index += 1

    return index


def _is_module_docstring(node: Any) -> bool:
    cst = _load_libcst()
    if not isinstance(node, cst.SimpleStatementLine) or len(node.body) != 1:
        return False
    statement = node.body[0]
    return isinstance(statement, cst.Expr) and isinstance(statement.value, cst.SimpleString)


def _is_future_import(node: Any) -> bool:
    cst = _load_libcst()
    if not isinstance(node, cst.SimpleStatementLine) or len(node.body) != 1:
        return False
    statement = node.body[0]
    if not isinstance(statement, cst.ImportFrom):
        return False
    return _module_name(statement.module) == "__future__"
