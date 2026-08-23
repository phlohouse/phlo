"""Tests for compiled RBAC verification in startup validation.

Missing compilers, unloadable RBAC config, and drift between compiled policy
and backend state are recorded as warnings on the validation report.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from phlo.security.validation import RegulatedValidationReport, _verify_compiled_rbac


def _empty_report() -> RegulatedValidationReport:
    return RegulatedValidationReport(regulated_enabled=True, passed=True)


class TestVerifyCompiledRbac:
    @patch("phlo.security.validation.RBACConfigLoader")
    @patch("phlo.security.validation.COMPILER_REGISTRY", {})
    def test_no_compilers_registered(self, mock_loader_cls):
        mock_loader_cls.return_value.load.return_value = MagicMock()
        report = _empty_report()
        _verify_compiled_rbac(report)
        assert any("no backend compilers" in w for w in report.warnings)

    @patch("phlo.security.validation.RBACConfigLoader")
    def test_rbac_load_failure(self, mock_loader_cls):
        mock_loader_cls.return_value.load.side_effect = RuntimeError("no config")
        report = _empty_report()
        _verify_compiled_rbac(report)
        assert any("could not load RBAC config" in w for w in report.warnings)

    @patch("phlo.security.validation.RBACConfigLoader")
    @patch("phlo.security.validation.COMPILER_REGISTRY")
    def test_backend_in_sync(self, mock_registry, mock_loader_cls):
        mock_loader_cls.return_value.load.return_value = MagicMock()
        mock_compiler = MagicMock()
        mock_compiler.return_value.verify.return_value = MagicMock(
            in_sync=True, missing=(), extra=(), mismatched=()
        )
        mock_registry.items.return_value = [("trino", mock_compiler)]
        report = _empty_report()
        _verify_compiled_rbac(report)
        assert len(report.warnings) == 0

    @patch("phlo.security.validation.RBACConfigLoader")
    @patch("phlo.security.validation.COMPILER_REGISTRY")
    def test_backend_out_of_sync(self, mock_registry, mock_loader_cls):
        mock_loader_cls.return_value.load.return_value = MagicMock()
        missing_artifact = MagicMock()
        missing_artifact.name = "GRANT SELECT ON iceberg.bronze.*"
        mock_compiler = MagicMock()
        mock_compiler.return_value.verify.return_value = MagicMock(
            in_sync=False, missing=(missing_artifact,), extra=(), mismatched=()
        )
        mock_registry.items.return_value = [("trino", mock_compiler)]
        report = _empty_report()
        _verify_compiled_rbac(report)
        assert len(report.warnings) == 1
        assert "out of sync" in report.warnings[0]
        assert "1 missing" in report.warnings[0]

    @patch("phlo.security.validation.RBACConfigLoader")
    @patch("phlo.security.validation.COMPILER_REGISTRY")
    def test_verify_not_implemented(self, mock_registry, mock_loader_cls):
        mock_loader_cls.return_value.load.return_value = MagicMock()
        mock_compiler = MagicMock()
        mock_compiler.return_value.verify.side_effect = NotImplementedError
        mock_registry.items.return_value = [("hasura", mock_compiler)]
        report = _empty_report()
        _verify_compiled_rbac(report)
        assert any("not implemented" in w for w in report.warnings)

    @patch("phlo.security.validation.RBACConfigLoader")
    @patch("phlo.security.validation.COMPILER_REGISTRY")
    def test_verify_connection_error(self, mock_registry, mock_loader_cls):
        mock_loader_cls.return_value.load.return_value = MagicMock()
        mock_compiler = MagicMock()
        mock_compiler.return_value.verify.side_effect = ConnectionError("refused")
        mock_registry.items.return_value = [("trino", mock_compiler)]
        report = _empty_report()
        _verify_compiled_rbac(report)
        assert any("refused" in w for w in report.warnings)

    @patch("phlo.security.validation.RBACConfigLoader")
    @patch("phlo.security.validation.COMPILER_REGISTRY")
    def test_warnings_do_not_fail_report(self, mock_registry, mock_loader_cls):
        mock_loader_cls.return_value.load.return_value = MagicMock()
        mock_compiler = MagicMock()
        mock_compiler.return_value.verify.side_effect = NotImplementedError
        mock_registry.items.return_value = [("nessie", mock_compiler)]
        report = _empty_report()
        _verify_compiled_rbac(report)
        assert report.passed is True
        assert len(report.warnings) == 1

    @patch("phlo.security.validation.RBACConfigLoader")
    @patch("phlo.security.validation.COMPILER_REGISTRY")
    def test_multiple_backends(self, mock_registry, mock_loader_cls):
        mock_loader_cls.return_value.load.return_value = MagicMock()

        trino_compiler = MagicMock()
        trino_compiler.return_value.verify.return_value = MagicMock(
            in_sync=True, missing=(), extra=(), mismatched=()
        )
        pg_compiler = MagicMock()
        pg_compiler.return_value.verify.side_effect = NotImplementedError

        mock_registry.items.return_value = [
            ("postgres", pg_compiler),
            ("trino", trino_compiler),
        ]
        report = _empty_report()
        _verify_compiled_rbac(report)
        assert len(report.warnings) == 1
        assert "postgres" in report.warnings[0]
