"""Example Observatory extension plugin for Phlo.

This package demonstrates how to build a custom Observatory UI extension
that integrates with the Phlo data platform. It provides example implementations
of routes, navigation items, dashboard slots, and settings panels.

Example:
    The extension is automatically discovered by Phlo's plugin system
    when the package is installed. No manual registration required.

    To test the extension locally::

        pip install -e /path/to/phlo-observatory-example
        phlo services start

"""

from importlib.metadata import version

__version__ = version("phlo-observatory-example")
