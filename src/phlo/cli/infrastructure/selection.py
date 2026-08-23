"""Resolve the final service set to install from defaults and CLI overrides.

Selection order is stable: defaults first, then explicitly enabled names in
CLI order, then profile services in discovery order. Explicitly disabled
names are removed from every source, and duplicates across sources are
deduplicated by name.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from phlo.plugins.discovery import ServiceDefinition


def select_services_to_install(
    *,
    all_services: Mapping[str, ServiceDefinition],
    default_services: Iterable[ServiceDefinition],
    enabled_names: Iterable[str],
    disabled_names: Iterable[str],
) -> list[ServiceDefinition]:
    """Resolve final service selection from defaults and CLI enable/disable overrides.

    Order is stable: defaults first, then explicitly enabled names in CLI
    order, then profile services in discovery order; names are deduplicated
    across sources and `disabled_names` wins.
    """
    disabled = set(disabled_names)
    services_to_install: list[ServiceDefinition] = [
        service for service in default_services if service.name not in disabled
    ]
    seen_names = {service.name for service in services_to_install}

    for name in enabled_names:
        service = all_services.get(name)
        if service is None or name in disabled or name in seen_names:
            continue
        services_to_install.append(service)
        seen_names.add(name)

    for service in all_services.values():
        if service.profile and service.name not in disabled and service.name not in seen_names:
            services_to_install.append(service)
            seen_names.add(service.name)

    return services_to_install
