# Example lakehouses

The repository contains complete, standalone examples under `examples/lakehouses/`. Each example owns its environment, fixtures, workflows, and tests. Start with its README; commands and service requirements differ by example.

## Choose an example

| Example | Best for | Main pattern |
| --- | --- | --- |
| [Retail Files](../../examples/lakehouses/retail-files/README.md) | First complete example | Packaged blueprint with deterministic file ingestion |
| [E-commerce Replication](../../examples/lakehouses/ecommerce-replication/README.md) | Database ingestion | PostgreSQL replication, checks, and dbt marts |
| [SaaS Product Analytics](../../examples/lakehouses/saas-product-analytics/README.md) | REST APIs | Paginated API replay, schema evolution, and sessions |
| [Customer 360](../../examples/lakehouses/customer360/README.md) | Multi-domain modelling | Commerce, support, and marketing identity resolution |
| [IoT Telemetry](../../examples/lakehouses/iot-telemetry/README.md) | Pipeline stages | Compressed telemetry through normalise, aggregate, and publish stages |
| [Delta Portability](../../examples/lakehouses/delta-portability/README.md) | Alternative table format | IoT-shaped pipeline implemented with Delta Lake |
| [ClickHouse Ops](../../examples/lakehouses/clickhouse-ops/README.md) | Operational analytics | ClickHouse storage, access logs, events, and operational marts |
| [Logistics Control Tower](../../examples/lakehouses/logistics-control-tower/README.md) | Mixed sources | Sling, API, and file sources converging on shared shipment models |
| [Market Data and FX](../../examples/lakehouses/market-data-fx/README.md) | Multiple APIs | Equities and FX replay with domain-oriented models |
| [Public Data Research](../../examples/lakehouses/public-data-research/README.md) | Public-shaped feeds | Multiple replay APIs and research-oriented transforms |
| [Healthcare Claims](../../examples/lakehouses/healthcare-claims/README.md) | Regulated-domain modelling | Claims, eligibility, provider data, privacy contracts, and quality |
| [Federated Domains](../../examples/lakehouses/federated-domains/README.md) | Team boundaries | Independent domain projects and explicit federation constraints |
| [Polaris Streaming](../../examples/lakehouses/polaris-streaming/README.md) | Streaming stack exploration | Kafka, Airbyte, Polaris, and supporting control planes |
| [WAP Failure Lab](../../examples/lakehouses/wap-failure-lab/README.md) | Failure and recovery | Deliberate write-audit-publish failures with recorded evidence |

## Use an example safely

1. Read the example's prerequisites and expected results.
2. Run commands from that example directory, not the repository root.
3. Use its lock file and documented environment rather than the root workspace environment.
4. Generate deterministic fixtures before starting services when instructed.
5. Run the focused test before changing providers or workflow layout.
6. Treat recorded evidence and pinned commits as historical proof, not current release status.

The machine-readable catalogue is [`examples/lakehouses/catalog.json`](../../examples/lakehouses/catalog.json). It is intended for documentation tools and agents that need to discover examples without scraping prose.
