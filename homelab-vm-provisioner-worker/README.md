# Homelab VM Provisioner Worker

Long-running daemon for processing VM provisioning jobs from PostgreSQL.

## Overview

The worker daemon is a standalone Python service that claims and executes queued provisioning jobs. It is designed to run on the same host as the VMs being provisioned and must have access to the `vmctl` command-line tool from the [homelab-vm-provisioner-cli](../homelab-vm-provisioner-cli) project.

## Features

- Claims jobs only for configured `HOST_ID` (never processes other hosts' jobs)
- Concurrent job execution (configurable via `PROVISIONER_CONCURRENCY`)
- Resource locking prevents conflicting concurrent operations
- Graceful shutdown (waits for active jobs to complete)
- Automatic retry for transient failures
- Comprehensive event logging per job

## Quick Start

```bash
# Setup (install dependencies)
./setup

# Configure environment
cp .env.example .env
# Edit .env and set HOST_ID, DB_SERVICE_URL, DB_SERVICE_PASSWORD

# Start worker (requires sudo for libvirt/nftables access)
./start
```

**Note**: The worker requires sudo access to execute provisioner CLI operations that interact with libvirt and nftables. You will be prompted for your sudo password on startup.

## Configuration

The worker reads configuration from environment variables:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `HOST_ID` | Yes | - | Host identifier for job claiming |
| `DB_SERVICE_URL` | Yes | - | Database microservice URL |
| `DB_SERVICE_PASSWORD` | Yes | - | Database microservice password |
| `PROVISIONER_CLI_PATH` | No | PATH lookup | Path to provisioner CLI directory |
| `WORKER_ID` | No | auto-generated | Unique worker identifier (hostname-PID) |
| `PROVISIONER_CONCURRENCY` | No | 1 | Maximum concurrent jobs |
| `WORKER_POLL_INTERVAL` | No | 5.0 | Poll interval in seconds |

### Monorepo Configuration

When running in the monorepo, set `PROVISIONER_CLI_PATH` to point to the local provisioner CLI:

```bash
PROVISIONER_CLI_PATH=../homelab-vm-provisioner-cli
```

This allows the worker to use the monorepo's provisioner without requiring it to be installed system-wide.

## Supported Job Types

- `provision_vm`: Create a new VM from a config file
- `destroy_vm`: Destroy an existing VM
- `clone_vm`: Clone a VM disk to create a new VM
- `start_vm`: Start a stopped VM
- `stop_vm`: Stop a running VM
- `reconcile_vm_networking`: Reconcile network configuration and firewall rules
- `snapshot_create`: Create a VM snapshot
- `snapshot_restore`: Restore a VM from a snapshot
- `snapshot_delete`: Delete a VM snapshot

## Resource Locking

The worker uses resource locks to prevent concurrent operations on the same resource:

- **VM locks** (`vm:<vm_id>`): Lock specific VMs during mutation operations
- **Host locks** (`host:<host_id>`): Lock entire host for host-level operations
- **Firewall locks** (`firewall:<host_id>`): Lock firewall during network reconciliation
- **Network locks** (`network:<host_id>`): Lock network during network reconciliation

Locks are sorted alphabetically for deterministic ordering to prevent deadlocks.

## Concurrency

The worker supports concurrent job execution up to `PROVISIONER_CONCURRENCY`:

- Jobs for different VMs can run concurrently
- Jobs requiring the same locks are serialized
- Failed lock acquisition causes job retry (not failure)

**Recommendation**: Start with `PROVISIONER_CONCURRENCY=1` and increase cautiously. Concurrent provisioning can be resource-intensive.

## Testing

Run tests:

```bash
# Run all tests
./scripts/test

# Run tests with coverage (requires 80% minimum)
./scripts/coverage

# Run specific test module
.venv/bin/python -m unittest tests.test_worker
```

**Coverage requirement**: The project enforces 80% minimum test coverage. The `./scripts/coverage` script will fail if coverage drops below this threshold.

## Deployment

### Standalone Deployment

The worker can be deployed independently on any host:

1. Install the worker: `./setup`
2. Configure environment variables in `.env`
3. Ensure `vmctl` is in PATH or set `PROVISIONER_CLI_PATH`
4. Start the worker: `./start`

### Monorepo Deployment

When running as part of the monorepo:

1. Configure `.env` with `PROVISIONER_CLI_PATH=../homelab-vm-provisioner-cli`
2. Use monorepo scripts: `./start --enable-worker` (from workspace root)

### Systemd Service (Future)

The worker is designed to run as a systemd service. Service file will be added in a future update.

## Architecture

```
Worker Daemon
   ↓ (claims jobs)
Database Microservice
   ↓ (queries)
PostgreSQL
```

The worker:
1. Polls database microservice for queued jobs matching `HOST_ID`
2. Claims jobs using row-level locking (safe for concurrent workers)
3. Acquires resource locks before execution
4. Executes job by calling `vmctl` command
5. Updates job status and appends events
6. Releases resource locks

## Limitations

- Cannot be dockerized (must run on host for libvirt access)
- No Unix socket wakeup (polls every `WORKER_POLL_INTERVAL` seconds)
- No automatic stale job recovery (manual intervention required)
- No authentication/authorization (assumes trusted environment)

## Development

Project structure:

```
hlvmp_worker/
  ├── __init__.py
  ├── config.py        # Configuration management
  ├── db_client.py     # Database microservice client
  ├── executor.py      # Job executor (calls vmctl)
  └── worker.py        # Main worker daemon

tests/
  ├── test_config.py
  ├── test_db_client.py
  ├── test_executor.py
  └── test_worker.py
```

## License

MIT
