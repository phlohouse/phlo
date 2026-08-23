"""
Phlo Exception Classes

Structured error classes with error codes, contextual messages, and suggestions.
"""

import re
from enum import Enum

_KEY_VALUE_SENSITIVE_PATTERN = re.compile(
    r"\b(password|passwd|token|secret|api_key|apikey|credential)\b\s*[:=]\s*[^\s,;]+",
    re.IGNORECASE,
)
_AUTHORIZATION_SENSITIVE_PATTERN = re.compile(r"\b(authorization|bearer)\b\s+\S+", re.IGNORECASE)
_CONNECTION_STRING_SENSITIVE_PATTERN = re.compile(
    r"\b(connection\s+string)\b\s*[:=]\s*.+?(?=(?:[,;]\s+\w+\s*[:=])|\n|$)",
    re.IGNORECASE,
)
_KEY_MATERIAL_SENSITIVE_PATTERN = re.compile(
    r"\b(private_key|signing_key|encryption_key)\b(?:\s*[:=]\s*|\s+).+?(?=(?:[,;]\s+\w+\s*[:=])|\n|$)",
    re.IGNORECASE,
)
_URL_CREDENTIALS_SENSITIVE_PATTERN = re.compile(
    r"\b([a-z][a-z0-9+.-]*://[^:\s/@]+:)[^@\s]+@",
    re.IGNORECASE,
)
_URL_TOKEN_USERINFO_SENSITIVE_PATTERN = re.compile(
    r"\b([a-z][a-z0-9+.-]*://)[^:\s/@]+@",
    re.IGNORECASE,
)


def redact_sensitive_text(s: str) -> str:
    """Redact sensitive patterns from a string for safe output."""
    result = _KEY_MATERIAL_SENSITIVE_PATTERN.sub(r"\1=<redacted>", s)
    result = _CONNECTION_STRING_SENSITIVE_PATTERN.sub(r"\1=<redacted>", result)
    result = _URL_CREDENTIALS_SENSITIVE_PATTERN.sub(r"\1<redacted>@", result)
    result = _URL_TOKEN_USERINFO_SENSITIVE_PATTERN.sub(r"\1<redacted>@", result)
    result = _KEY_VALUE_SENSITIVE_PATTERN.sub(
        lambda m: f"{m.group(1)}=<redacted>",
        result,
    )
    return _AUTHORIZATION_SENSITIVE_PATTERN.sub(r"\1 <redacted>", result)


def _redact_sensitive(s: str) -> str:
    return redact_sensitive_text(s)


class PhloErrorCode(Enum):
    """Error codes for Phlo exceptions."""

    # Discovery and Configuration Errors (PHLO-001 to PHLO-099)
    ASSET_NOT_DISCOVERED = "PHLO-001"
    SCHEMA_MISMATCH = "PHLO-002"
    INVALID_CRON = "PHLO-003"
    VALIDATION_FAILED = "PHLO-004"
    MISSING_SCHEMA = "PHLO-005"

    # Runtime and Integration Errors (PHLO-006 to PHLO-008)
    INGESTION_FAILED = "PHLO-006"
    TABLE_NOT_FOUND = "PHLO-007"
    INFRASTRUCTURE_ERROR = "PHLO-008"

    # Schema and Type Errors (PHLO-200 to PHLO-299)
    SCHEMA_CONVERSION_ERROR = "PHLO-200"
    TYPE_CONVERSION_ERROR = "PHLO-201"

    # DLT Errors (PHLO-300 to PHLO-399)
    DLT_PIPELINE_FAILED = "PHLO-300"
    DLT_SOURCE_ERROR = "PHLO-301"

    # Iceberg Errors (PHLO-400 to PHLO-499)
    ICEBERG_CATALOG_ERROR = "PHLO-400"
    ICEBERG_TABLE_ERROR = "PHLO-401"
    ICEBERG_WRITE_ERROR = "PHLO-402"


class PhloError(Exception):
    """
    Base exception for Phlo framework errors.

    All Phlo exceptions include:
    - Error code for searchability
    - Contextual error message
    - Suggested actions to resolve
    - Link to documentation

    Example:
        raise PhloError(
            message="unique_key 'observation_id' not found in schema",
            code=PhloErrorCode.SCHEMA_MISMATCH,
            suggestions=[
                "Check that unique_key matches a field in validation_schema",
                "Available fields: id, city, temperature, timestamp",
            ]
        )
    """

    def __init__(
        self,
        message: str,
        code: PhloErrorCode,
        suggestions: list[str] | None = None,
        cause: Exception | None = None,
    ):
        """
        Initialize PhloError; formats the message with code, suggestions,
        cause, and a documentation link.
        """
        self.code = code
        self.suggestions = suggestions or []
        self.cause = cause
        self.doc_url = f"https://docs.phlo.dev/errors/{code.value}"

        # Build formatted error message
        full_message = self._format_message(message)

        super().__init__(full_message)

    def _format_message(self, message: str) -> str:
        """Format error message with code, suggestions, and documentation link."""

        lines = [
            f"{self.__class__.__name__} ({self.code.value}): {message}",
        ]

        if self.suggestions:
            lines.append("")
            lines.append("Suggested actions:")
            for i, suggestion in enumerate(self.suggestions, 1):
                lines.append(f"  {i}. {suggestion}")

        if self.cause:
            lines.append("")
            lines.append(
                f"Caused by: {type(self.cause).__name__}: {_redact_sensitive(str(self.cause))}"
            )

        lines.append("")
        lines.append(f"Documentation: {self.doc_url}")

        return "\n".join(lines)


# Specific Error Classes


class PhloDiscoveryError(PhloError):
    """Raised when assets cannot be discovered by Dagster."""

    def __init__(
        self,
        message: str,
        suggestions: list[str] | None = None,
        cause: Exception | None = None,
    ):
        """Initialize a discovery error."""
        super().__init__(
            message=message,
            code=PhloErrorCode.ASSET_NOT_DISCOVERED,
            suggestions=suggestions,
            cause=cause,
        )


class PhloValidationError(PhloError):
    """Raised when data validation fails."""

    def __init__(
        self,
        message: str,
        suggestions: list[str] | None = None,
        cause: Exception | None = None,
    ):
        """Initialize a validation error."""
        super().__init__(
            message=message,
            code=PhloErrorCode.VALIDATION_FAILED,
            suggestions=suggestions,
            cause=cause,
        )


class PhloConfigError(PhloError):
    """Raised when decorator configuration is invalid."""

    def __init__(self, message: str, suggestions: list[str] | None = None):
        """Initialize a configuration error."""
        super().__init__(
            message=message,
            code=PhloErrorCode.MISSING_SCHEMA,
            suggestions=suggestions,
        )


class PhloIngestionError(PhloError):
    """Raised when data ingestion fails."""

    def __init__(
        self,
        message: str,
        suggestions: list[str] | None = None,
        cause: Exception | None = None,
    ):
        """Initialize an ingestion error."""
        super().__init__(
            message=message,
            code=PhloErrorCode.INGESTION_FAILED,
            suggestions=suggestions,
            cause=cause,
        )


class PhloTableError(PhloError):
    """Raised when Iceberg table operations fail."""

    def __init__(self, message: str, suggestions: list[str] | None = None):
        """Initialize a table operation error."""
        super().__init__(
            message=message,
            code=PhloErrorCode.TABLE_NOT_FOUND,
            suggestions=suggestions,
        )


class PhloInfrastructureError(PhloError):
    """Raised when infrastructure services are unavailable."""

    def __init__(
        self,
        message: str,
        suggestions: list[str] | None = None,
        cause: Exception | None = None,
    ):
        """Initialize an infrastructure error."""
        super().__init__(
            message=message,
            code=PhloErrorCode.INFRASTRUCTURE_ERROR,
            suggestions=suggestions,
            cause=cause,
        )


class PhloCapabilitySetupError(PhloError):
    """Raised when a capability is present but cannot be set up correctly."""

    def __init__(
        self,
        capability: str,
        message: str,
        *,
        required: bool,
        suggestions: list[str] | None = None,
        cause: Exception | None = None,
    ):
        self.capability = capability
        self.required = required
        super().__init__(
            message=message,
            code=PhloErrorCode.INFRASTRUCTURE_ERROR,
            suggestions=suggestions,
            cause=cause,
        )


class SchemaConversionError(PhloError):
    """Raised when Pandera schema cannot be converted to PyIceberg."""

    def __init__(self, message: str, suggestions: list[str] | None = None):
        """Initialize a schema conversion error."""
        super().__init__(
            message=message,
            code=PhloErrorCode.SCHEMA_CONVERSION_ERROR,
            suggestions=suggestions,
        )


class DLTPipelineError(PhloError):
    """Raised when DLT pipeline execution fails."""

    def __init__(
        self,
        message: str,
        suggestions: list[str] | None = None,
        cause: Exception | None = None,
    ):
        """Initialize a DLT pipeline error."""
        super().__init__(
            message=message,
            code=PhloErrorCode.DLT_PIPELINE_FAILED,
            suggestions=suggestions,
            cause=cause,
        )


class IcebergCatalogError(PhloError):
    """Raised when Iceberg catalog operations fail."""

    def __init__(
        self,
        message: str,
        suggestions: list[str] | None = None,
        cause: Exception | None = None,
    ):
        """Initialize an Iceberg catalog error."""
        super().__init__(
            message=message,
            code=PhloErrorCode.ICEBERG_CATALOG_ERROR,
            suggestions=suggestions,
            cause=cause,
        )


# Utility Functions for Error Suggestions


def suggest_similar_field_names(
    invalid_field: str,
    valid_fields: list[str],
    max_suggestions: int = 3,
) -> list[str]:
    """Generate "Did you mean?" suggestions for field name typos.

    Uses fuzzy matching against valid_fields, returning at most
    max_suggestions; falls back to listing all valid fields when nothing
    is close enough.
    """
    from difflib import get_close_matches

    similar = get_close_matches(
        invalid_field,
        valid_fields,
        n=max_suggestions,
        cutoff=0.6,  # Similarity threshold (0-1)
    )

    if similar:
        return [f"Did you mean '{field}'?" for field in similar]
    return [f"Available fields: {', '.join(valid_fields)}"]


def format_field_list(fields: list[str]) -> str:
    """Format a list of fields for error messages."""
    return ", ".join(f"'{field}'" for field in fields)
