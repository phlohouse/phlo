"""Registry payload validation shared by CLI and Observatory readers."""

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class RegistryPayloadError(ValueError):
    """A registry document is malformed."""


class RegistryServiceEntry(BaseModel):
    """Registry package metadata; service entries use type='service'."""

    model_config = ConfigDict(strict=True, frozen=True, extra="allow")

    name: str = Field(default="", exclude=True)
    type: str = Field(min_length=1)
    package: str = Field(min_length=1)
    version: str = ""
    description: str = ""
    author: str = ""
    homepage: str | None = None
    tags: list[str] = Field(default_factory=list)
    verified: bool = False
    core: bool = False


class RegistryDocument(BaseModel):
    """Validated snapshot of a registry document."""

    model_config = ConfigDict(strict=True, frozen=True, extra="allow")
    plugins: dict[str, RegistryServiceEntry]


def parse_registry(payload: object) -> RegistryDocument:
    """Reject malformed entries rather than dropping them from the snapshot."""
    if not isinstance(payload, dict) or "plugins" not in payload:
        raise RegistryPayloadError("Registry payload missing plugins section.")
    try:
        document = RegistryDocument.model_validate(payload)
    except ValidationError as exc:
        raise RegistryPayloadError("Registry payload contains invalid plugin entries.") from exc
    return document.model_copy(
        update={
            "plugins": {
                name: entry.model_copy(update={"name": name})
                for name, entry in document.plugins.items()
            }
        }
    )
