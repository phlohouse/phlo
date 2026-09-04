"""RustFS service plugin package.

This package provides a Phlo plugin for integrating RustFS (Rust-based S3-compatible
object storage) into the data platform. It exposes service definitions for running
RustFS containers and bucket initialization, along with resource providers for
S3-compatible storage capabilities.

Exports:
    RustfsServicePlugin: Main service plugin for running RustFS container.
    RustfsSettings: Configuration settings for RustFS connectivity.
    get_settings: Cached factory function returning RustfsSettings instance.

Example:
    >>> from phlo_rustfs import RustfsSettings, get_settings
    >>> settings = get_settings()
    >>> print(settings.rustfs_endpoint())
    "localhost:9000"

"""

from phlo_rustfs.plugin import RustfsServicePlugin
from phlo_rustfs.settings import RustfsSettings, get_settings

__all__ = ["RustfsServicePlugin", "RustfsSettings", "get_settings"]
from importlib.metadata import version

__version__ = version("phlo-rustfs")
