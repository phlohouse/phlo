"""Micro-benchmarks for discovery and registry hot paths.

Benchmarks service YAML discovery and registry-client payload handling with
plugin entry-point scans disabled, reporting percentile timings per scenario
after warmup. Fixture services and registry payloads are generated in-process
so runs do not depend on repository state.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

# Prevent import-time plugin entry-point scans from polluting benchmark runs.
os.environ.setdefault("PHLO_NO_AUTO_DISCOVER", "1")

from phlo.plugins import registry_client
from phlo.plugins.discovery.services import ServiceDiscovery


@dataclass(slots=True)
class BenchmarkResult:
    """Summary for one benchmark scenario."""

    name: str
    iterations: int
    warmups: int
    mean_ms: float
    p50_ms: float
    p95_ms: float
    min_ms: float
    max_ms: float


class BenchmarkServiceDiscovery(ServiceDiscovery):
    """Service discovery variant that skips plugin entry-point loading."""

    def _load_service_plugins(self) -> None:  # pragma: no cover - intentionally empty
        return


def _percentile(sorted_samples: list[float], fraction: float) -> float:
    if not sorted_samples:
        raise ValueError("Cannot compute percentile for empty sample set.")
    if len(sorted_samples) == 1:
        return sorted_samples[0]

    index = (len(sorted_samples) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(sorted_samples) - 1)
    weight = index - lower
    return sorted_samples[lower] * (1 - weight) + sorted_samples[upper] * weight


def _run_microbenchmark(
    name: str,
    operation: Callable[[], None],
    iterations: int,
    warmups: int,
) -> BenchmarkResult:
    for _ in range(warmups):
        operation()

    samples_ms: list[float] = []
    for _ in range(iterations):
        started_ns = time.perf_counter_ns()
        operation()
        elapsed_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
        samples_ms.append(elapsed_ms)

    ordered = sorted(samples_ms)
    return BenchmarkResult(
        name=name,
        iterations=iterations,
        warmups=warmups,
        mean_ms=statistics.fmean(samples_ms),
        p50_ms=_percentile(ordered, 0.50),
        p95_ms=_percentile(ordered, 0.95),
        min_ms=ordered[0],
        max_ms=ordered[-1],
    )


def _write_service_fixture(services_root: Path, service_count: int) -> None:
    services_root.mkdir(parents=True, exist_ok=True)

    for index in range(service_count):
        service_name = f"benchmark-service-{index:03d}"
        service_dir = services_root / f"group-{index % 8:02d}" / service_name
        service_dir.mkdir(parents=True, exist_ok=True)

        depends_on = "[]" if index == 0 else f"[benchmark-service-{index - 1:03d}]"

        service_yaml = (
            f"name: {service_name}\n"
            f"description: Synthetic benchmark service {index}\n"
            "category: benchmark\n"
            "default: false\n"
            f"depends_on: {depends_on}\n"
            "compose:\n"
            "  image: busybox:latest\n"
        )
        (service_dir / "service.yaml").write_text(service_yaml, encoding="utf-8")


def _build_registry_payload(plugin_count: int) -> dict[str, Any]:
    plugins = {
        f"benchmark-plugin-{index:03d}": {
            "type": "service",
            "package": f"phlo-benchmark-plugin-{index:03d}",
            "version": "1.0.0",
            "description": f"Synthetic benchmark plugin {index}",
            "author": "phlo",
            "homepage": "https://example.invalid",
            "tags": ["benchmark", "discovery"],
            "verified": True,
            "core": False,
        }
        for index in range(plugin_count)
    }
    return {"version": "1.0.0", "plugins": plugins}


@contextmanager
def _registry_benchmark_context(payload: dict[str, Any]) -> Iterator[None]:
    class BenchmarkSettings:
        plugin_registry_url = ""
        plugin_registry_cache_ttl_seconds = 3600
        plugin_registry_timeout_seconds = 1

    original_get_settings = registry_client.get_settings
    original_load_local = registry_client._load_registry_from_local

    registry_client.get_settings = lambda: BenchmarkSettings()  # type: ignore[assignment]
    registry_client._load_registry_from_local = lambda: payload  # type: ignore[assignment]
    registry_client.clear_registry_cache()

    try:
        yield
    finally:
        registry_client.get_settings = original_get_settings
        registry_client._load_registry_from_local = original_load_local
        registry_client.clear_registry_cache()


def run_suite(
    *,
    iterations: int,
    warmups: int,
    service_count: int,
    registry_plugin_count: int,
) -> list[BenchmarkResult]:
    """Run discovery and registry benchmarks against generated fixtures."""
    results: list[BenchmarkResult] = []

    with TemporaryDirectory(prefix="phlo-discovery-bench-") as temp_dir:
        services_dir = Path(temp_dir) / "services"
        _write_service_fixture(services_dir, service_count)

        def service_discovery_cold() -> None:
            discovery = BenchmarkServiceDiscovery(services_dir=services_dir)
            discovery.discover()

        warm_discovery = BenchmarkServiceDiscovery(services_dir=services_dir)
        warm_discovery.discover()

        def service_discovery_warm_cache() -> None:
            warm_discovery.discover()

        refresh_discovery = BenchmarkServiceDiscovery(services_dir=services_dir)
        refresh_discovery.discover()

        def service_discovery_refresh() -> None:
            refresh_discovery.discover(refresh=True)

        results.append(
            _run_microbenchmark(
                name="service_discovery_cold",
                operation=service_discovery_cold,
                iterations=iterations,
                warmups=warmups,
            )
        )
        results.append(
            _run_microbenchmark(
                name="service_discovery_warm_cache",
                operation=service_discovery_warm_cache,
                iterations=iterations,
                warmups=warmups,
            )
        )
        results.append(
            _run_microbenchmark(
                name="service_discovery_refresh",
                operation=service_discovery_refresh,
                iterations=iterations,
                warmups=warmups,
            )
        )

    payload = _build_registry_payload(registry_plugin_count)
    with _registry_benchmark_context(payload):

        def registry_fetch_cold() -> None:
            registry_client.clear_registry_cache()
            registry_client.fetch_registry()

        registry_client.clear_registry_cache()
        registry_client.fetch_registry()

        def registry_fetch_warm_cache() -> None:
            registry_client.fetch_registry()

        def registry_fetch_refresh() -> None:
            registry_client.fetch_registry(force_refresh=True)

        results.append(
            _run_microbenchmark(
                name="registry_fetch_cold",
                operation=registry_fetch_cold,
                iterations=iterations,
                warmups=warmups,
            )
        )
        results.append(
            _run_microbenchmark(
                name="registry_fetch_warm_cache",
                operation=registry_fetch_warm_cache,
                iterations=iterations,
                warmups=warmups,
            )
        )
        results.append(
            _run_microbenchmark(
                name="registry_fetch_refresh",
                operation=registry_fetch_refresh,
                iterations=iterations,
                warmups=warmups,
            )
        )

    return results


def _print_results(results: list[BenchmarkResult]) -> None:
    print(
        f"{'scenario':<32} {'mean_ms':>12} {'p50_ms':>12} {'p95_ms':>12} {'min_ms':>12} {'max_ms':>12}"
    )
    for result in results:
        print(
            f"{result.name:<32} "
            f"{result.mean_ms:12.6f} "
            f"{result.p50_ms:12.6f} "
            f"{result.p95_ms:12.6f} "
            f"{result.min_ms:12.6f} "
            f"{result.max_ms:12.6f}"
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run micro-benchmarks for discovery and registry cache paths."
    )
    parser.add_argument("--iterations", type=int, default=300)
    parser.add_argument("--warmups", type=int, default=50)
    parser.add_argument("--service-count", type=int, default=120)
    parser.add_argument("--registry-plugin-count", type=int, default=250)
    parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="Optional path to write benchmark summaries as JSON.",
    )
    return parser.parse_args()


def main() -> int:
    """Parse CLI arguments, run the benchmark suite, and print results."""
    args = _parse_args()

    for field_name in ("iterations", "warmups", "service_count", "registry_plugin_count"):
        value = getattr(args, field_name)
        if value < 1:
            raise ValueError(f"--{field_name.replace('_', '-')} must be >= 1")

    results = run_suite(
        iterations=args.iterations,
        warmups=args.warmups,
        service_count=args.service_count,
        registry_plugin_count=args.registry_plugin_count,
    )
    _print_results(results)

    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "iterations": args.iterations,
            "warmups": args.warmups,
            "service_count": args.service_count,
            "registry_plugin_count": args.registry_plugin_count,
            "results": [asdict(result) for result in results],
        }
        args.json_output.write_text(f"{json.dumps(payload, indent=2)}\n", encoding="utf-8")
        print(f"\njson_report={args.json_output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
