# 29. CLI Services Enhancements: Restart Command and Profile Flag Fix

Date: 2025-12-21

## Status

Accepted

## Beads

- `phlo-6zp`: CLI: Add 'phlo services restart' command to simplify stop/start cycle
- `phlo-8z5`: CLI: '--profile' flag should only affect profile services, not restart all running services

## Context

Two related CLI improvements are needed for the `phlo services` command:

1. **Missing restart command**: Users need a convenient way to restart services without manually running `stop` followed by `start`. This is particularly useful during development when configuration changes require a full service restart.

2. **Profile flag behavior bug**: When running `phlo services start --profile observability`, the current implementation restarts **all** services (including core services like postgres, dagster, etc.) instead of just starting the observability profile services. The expected behavior is that `--profile` should only affect the profile-specific services without restarting already-running core services.

## Decision

### 1. Add `restart` Command

Add a new `restart` subcommand that combines `stop` and `start` in a single operation:

```bash
phlo services restart                          # Restart all services
phlo services restart --profile observability  # Restart profile services
phlo services restart --service postgres       # Restart specific service
phlo services restart --build                  # Rebuild before starting
```

The command will support all options common to both `stop` and `start`:

- `--profile`: Target specific profile services
- `--service`: Target specific service(s)
- `--build`: Rebuild images before starting
- `--dev`: Development mode mount

### 2. Fix `--profile` Flag Behavior

When `--profile` is specified without `--service`, the `start` command should only bring up the profile services, not restart all services. The fix involves:

1. When `--profile` is specified, extract the list of services belonging to that profile
2. Pass those services explicitly to `docker compose up` to only affect those containers

## Proposed Changes

### Core CLI

#### [MODIFY] [services.py](file:///Users/garethprice/Developer/phlo/src/phlo/cli/services.py)

1. Add new `restart` command (~50-70 lines):
   - Accept `--profile`, `--service`, `--build`, `--dev` options
   - Execute `stop` logic followed by `start` logic
   - Share common code with existing commands

2. Fix `start` command `--profile` behavior:
   - Add helper function `get_profile_services(profile_names)` to resolve profile names to service names
   - When `--profile` is specified without `--service`, automatically populate `services_list` with profile services
   - Use `docker compose up <services>` instead of `docker compose up` to target specific services

3. Fix `stop` command `--profile` behavior (same pattern):
   - When `--profile` is specified without `--service`, target only profile services

### Tests

#### [MODIFY] [test_cli_services.py](file:///Users/garethprice/Developer/phlo/tests/test_cli_services.py)

Add tests for:

- `get_profile_services()` helper function
- Verify profile flag produces correct service list

## Verification Plan

### Automated Tests

```bash
# Run the CLI services tests
pytest tests/test_cli_services.py -v

# Run full test suite to ensure no regressions
pytest tests/ -v --ignore=tests/integration
```

### Manual Verification

1. **Restart command**:

   ```bash
   cd phlo-examples/github
   phlo services start
   phlo services restart --service postgres  # Should restart only postgres
   phlo services status                       # Verify postgres restarted
   ```

2. **Profile flag fix**:
   ```bash
   phlo services start                        # Start core services
   phlo services start --profile observability # Should only start prometheus/grafana/loki/alloy
   docker ps                                   # Verify core services weren't restarted
   ```

## Consequences

- Users get a convenient single command for restarting services
- Profile flag behaves as expected, only affecting profile-specific services
- No breaking changes to existing commands
