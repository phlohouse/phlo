"""Kafka CLI commands: status and topic lag inspection."""

from __future__ import annotations

import click

from phlo.cli.output import command_failed_error


@click.command(name="kafka")
@click.argument("kafka_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def kafka_group(ctx: click.Context, kafka_args: tuple[str, ...]) -> None:
    """Interact with the Kafka broker (status, topics)."""
    args = list(kafka_args)
    if not args or args[0] in {"-h", "--help", "help"}:
        click.echo(ctx.get_help())
        return
    command = args.pop(0)
    if command == "status":
        _status()
        return
    if command == "topics":
        _topics()
        return
    click.echo(f"Unknown kafka command: {command}", err=True)
    ctx.exit(2)


def _status() -> None:
    from phlo_kafka.resource import KafkaResource

    client = KafkaResource()
    healthy = client.health_check()
    click.echo(f"Kafka health: {'ok' if healthy else 'unavailable'}")


def _topics() -> None:
    from phlo_kafka.resource import KafkaResource

    try:
        confluent_kafka = KafkaResource()._require_confluent()
        admin = confluent_kafka.AdminClient(
            {"bootstrap.servers": KafkaResource().bootstrap_servers}
        )
        metadata = admin.list_topics(timeout=10)
    except Exception as exc:
        command_failed_error(f"Could not list Kafka topics: {exc}")
    for topic in sorted(metadata.topics):
        click.echo(f"  - {topic}")
