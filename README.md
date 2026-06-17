# homelab-vm-provisioner-webapp

Top-level workspace for the homelab VM provisioner web application.

This repository wires together:

- `homelab-vm-provisioner-api/`: the Express API and nested Python provisioner integration
- `homelab-vm-provisioner-client/`: the React UI
- `homelab-vm-provisioner-proxy/`: a dead-simple reverse proxy that serves the React client and proxies API requests
- root scripts that install dependencies, build both apps, run tests, and deploy the client bundle into the proxy's `public/` directory

The sub-repositories already contain their own detailed READMEs. This document covers the root project workflow.

## Architecture

```text
Browser → Proxy (port 3000) → API (port 3001) → Python CLI → libvirt
         ↓
      Static Files (React app)
```

## Workspace Layout

```text
.
|- homelab-vm-provisioner-api/
|- homelab-vm-provisioner-client/
|- homelab-vm-provisioner-proxy/
|- scripts/
|  |- build-client-docker
|  |- build-proxy-docker
|  |- start-proxy-docker
|  `- test-docker-mode
|- .env.example
|- DOCKER.md
|- setup
|- build
|- start
`- README.md
```

## Requirements

- Git with submodule support
- Node.js 18+
- npm
- Python 3 with `venv`
- Docker (optional, for containerized client builds)
- A Linux libvirt host if you want actual VM lifecycle operations to work end-to-end
- `sudo` access for the account running the API

Notes:

- Initialize submodules before the first root setup run.
- The workspace setup installs all system dependencies on supported Linux distributions: git, Node.js 18+, npm, Python, libvirt, qemu, nftables, and other VM provisioning tools.
- Supported distributions: Ubuntu/Debian, Fedora, RHEL/Rocky/AlmaLinux, Arch Linux.
- On machines where system packages are already installed, use `./setup --skip-system-packages`.

## Setup

### Fresh workspace setup

If you did not clone the repository with submodules, initialize them first:

```bash
git submodule update --init --recursive
```

Then run:

```bash
./setup
```

For Docker deployment mode:

```bash
./setup --docker
```

If you are cloning fresh, `git clone --recurse-submodules <repo>` is the simplest starting point.

What it does:

- installs all system packages: git, Node.js 18+, npm, Python, libvirt, qemu, nftables, cloud-image-utils (on supported Linux distributions)
- enables and starts the libvirtd service
- initializes git submodules
- sets up the nested Python provisioner virtual environment
- installs API npm dependencies
- installs client npm dependencies (skipped in `--docker` mode unless `--dev` is also specified)
- installs proxy npm dependencies (skipped in `--docker` mode unless `--dev` is also specified)
- installs Playwright Chromium for client e2e tests (skipped in `--docker` mode unless `--dev` is also specified)

If your machine already has the required OS packages, use:

```bash
./setup --skip-system-packages
```

Useful pass-through options supported by the nested provisioner setup:

- `--skip-system-packages`: skip system package installation
- `--dev`: install Python dev dependencies (ruff, coverage, Sphinx)
- `--python <binary>`: specify Python interpreter

Note: The `--dev` flag installs dev dependencies in the Python provisioner and, when combined with `--docker`, also installs client and proxy npm packages on the host for testing (since tests don't run in containers).

Example:

```bash
./setup --skip-system-packages --python python3
```

## Configuration

The root project mostly works out of the box. The main configuration points are the root scripts and a small set of environment variables.

### Root scripts

- `./setup`: installs dependencies and configures environments (git submodules, Python venv, npm packages, Playwright)
- `./setup --docker`: setup for Docker mode (skips client and proxy npm installs since they run in containers)
- `./setup --dev`: setup with dev dependencies for the Python provisioner
- `./setup --docker --dev`: Docker mode with dev dependencies installed on host for testing
- `./setup --client-only`: setup for client-only development (skips API/provisioner, installs only client and proxy)
- `./build`: builds documentation and app artifacts (no tests)
- `./build --docker`: builds client static files using Docker instead of local Node.js
- `./build --client-only`: builds only client (skips API docs), useful for frontend-only development
- `./scripts/build-client-docker`: standalone script to build only client static files with Docker
- `./scripts/build-proxy-docker`: builds the proxy Docker image
- `./test-all`: runs all tests with coverage across Python CLI, API, and client
- `./start`: starts the API on port 3001 and the proxy on port 3000, serving the already-built client
- `./start --docker`: starts API locally and proxy in Docker container
- `./start --client-only`: builds client and starts proxy only (no API), useful for connecting to remote API
- `./start --client-only --docker`: builds client with Docker and runs proxy in Docker (no API)
- `./scripts/start-proxy-docker`: runs the proxy in a Docker container (requires static files in `public/`)

### Docker commands

```bash
# Setup for Docker mode
./setup --docker

# Start with Docker proxy (API on host)
./start --docker

# Or use individual scripts
./scripts/build-client-docker  # Build client static files
./scripts/build-proxy-docker   # Build proxy image
./scripts/start-proxy-docker   # Run proxy container
```

### Client-only workflow (frontend development)

For frontend developers who don't need the full API stack locally:

```bash
# One-time setup (skips Python provisioner and API dependencies)
./setup --client-only

# Build and start (connects to remote API)
./build --client-only
API_URL=http://192.168.1.100:3001 ./start --client-only

# Or combine with Docker
./setup --client-only --docker
./build --client-only --docker
./start --client-only --docker
```

**Configuration**: Copy `.env.example` to `.env` and customize:
```bash
cp .env.example .env
# Edit .env to set PROXY_PORT, API_PORT, API_URL, etc.
```

Or set environment variables directly:
```bash
PROXY_PORT=8080 API_PORT=8081 ./start --docker
```

### Client-only mode (remote API)

Use `--client-only` to run just the frontend proxy without starting the local API. This is useful for:
- Connecting to a remote API server
- Frontend development without running the full stack locally
- Multiple developers sharing a single API instance

```bash
# Build client and start proxy, connecting to remote API
API_URL=http://192.168.1.100:3001 ./start --client-only

# Or configure in .env
echo "API_URL=http://remote-server:3001" >> .env
./start --client-only

# With Docker proxy
./start --client-only --docker
```

The `--client-only` flag:
1. Builds the client (locally or with Docker if `--docker` is specified)
2. Deploys static files to the proxy
3. Starts only the proxy (skips API startup)
4. Uses `API_URL` environment variable to configure the remote API endpoint

### Environment variables

| Variable | Default | Used by | Purpose |
| --- | --- | --- | --- |
| `PROVISIONER_VENV_DIR` | `homelab-vm-provisioner-api/homelab-vm-provisioner/.venv` | `./setup`, `./start` | Location of the nested Python provisioner virtual environment |
| `PORT` | `3000` (proxy), `3001` (API) | Proxy and API | HTTP ports for the services |
| `API_URL` | `http://localhost:3001` | Proxy | Backend API URL for proxying |
| `HLVMP_PROVISIONER_DIR` | `homelab-vm-provisioner-api/homelab-vm-provisioner` | API | Override the nested provisioner checkout path |
| `HLVMP_API_RUNTIME_DIR` | `homelab-vm-provisioner-api/runtime` | API | Legacy runtime directory used for startup migration |
| `HLVMP_NETWORK_POOL_CIDR` | `10.80.0.0/16` | API | Global private pool used to allocate per-network-group subnets |
| `HLVMP_NETWORK_GROUP_PREFIX_LENGTH` | `28` | API | Prefix length allocated to each managed network group |
| `HLVMP_PYTHON_BIN` | set automatically by `./start` | API | Python executable used by the API bridge |
| `VITE_API_BASE_URL` | unset | client dev/build | Optional API base URL when running the client separately from the API |

Examples:

```bash
PROVISIONER_VENV_DIR="$HOME/.local/share/hlvmp/.venv" ./setup --skip-system-packages
PORT=4000 ./start
VITE_API_BASE_URL=http://localhost:3000 npm --prefix homelab-vm-provisioner-client run dev
```

### Generated and runtime paths

After setup and use, the workspace relies on these paths:

- `homelab-vm-provisioner-proxy/public/`: deployed client bundle served by the reverse proxy
- `homelab-vm-provisioner-api/homelab-vm-provisioner/configs/`: saved VM YAML configs
- `homelab-vm-provisioner-api/homelab-vm-provisioner/vm/metadata/`: persisted tenant and network-group records
- `homelab-vm-provisioner-api/homelab-vm-provisioner/vm/keys/users/`: uploaded SSH public keys
- `homelab-vm-provisioner-api/homelab-vm-provisioner/vm/data/`: provisioner VM data

## Tenant Networking

The workspace now uses tenant-owned network groups instead of one flat VM network.

- each network group maps to one libvirt network and one small subnet, default `/28`
- new groups allocate subnets from `HLVMP_NETWORK_POOL_CIDR`, default `10.80.0.0/16`
- VMs in the same group can talk by default
- hypervisor host access is allowed by default and can be disabled per VM
- cross-group traffic is denied by default
- private LAN access is denied by default and can be enabled per VM for admin-owned VMs
- internet egress is available through NAT-backed profiles without exposing private RFC1918 LANs by default

Managed VM and network-group policy now reconciles through application-owned nftables tables. See `homelab-vm-provisioner-api/docs/vm-networking-nftables.md`.

## Running

### Bundled root-project run

After `./setup` completes, build and start the workspace from the root with:

```bash
./build
./start
```

This command:

- verifies the built client exists in `homelab-vm-provisioner-api/public/`
- verifies the nested provisioner virtual environment exists
- exports `HLVMP_PYTHON_BIN` to the provisioner venv's Python interpreter
- starts the API, which also serves the built client

Open:

```text
http://localhost:3000
```

Notes:

- Start the API from an interactive terminal.
- On startup, the API may prompt for `sudo` so later `virsh` and libvirt operations can run correctly.

### Split development mode

If you want live frontend reloads, run the API and client separately.

Terminal 1:

```bash
./start
```

Terminal 2:

```bash
npm --prefix homelab-vm-provisioner-client run dev
```

Then open:

```text
http://localhost:5173
```

By default, the Vite dev server proxies `/api` and `/health` to `http://localhost:3000`.

If the API is running elsewhere, set `VITE_API_BASE_URL` before starting the client.

## Rebuilding

To rerun the root build without reinstalling dependencies:

```bash
./build
```

This runs:

- API tests, coverage, and docs build
- client tests, coverage, app build, and docs build
- client Playwright e2e tests
- client bundle deployment into the API `public/` directory

## Testing

To run tests across all subprojects and see a consolidated coverage report:

```bash
./test-all
```

This runs:

- Python CLI tests with coverage (homelab-vm-provisioner)
- Node.js API tests with coverage (homelab-vm-provisioner-api)
- React Client tests with coverage (homelab-vm-provisioner-client)

The script displays a color-coded summary showing:

- Test pass/fail status for each project
- Number of tests run
- Coverage percentage for each project
- Overall workspace test health

Example output:

```text
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  1/3 Python CLI (homelab-vm-provisioner)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✓ Python CLI
  Tests:    275 tests (all passed)
  Coverage: 86%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  2/3 Node.js API (homelab-vm-provisioner-api)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✓ Node.js API
  Tests:    111 tests passed
  Coverage: 87.09%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  3/3 React Client (homelab-vm-provisioner-client)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✓ React Client
  Tests:    31 tests passed
  Coverage: 74.64%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  SUMMARY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ✓ Python CLI: 275 tests (all passed), Coverage 86%
  ✓ Node.js API: 111 tests passed, Coverage 87.09%
  ✓ React Client: 31 tests passed, Coverage 74.64%

✓ All tests passed!
```

For individual project testing, see the respective subproject documentation.

## Subproject Docs

For service-specific details, see:

- `homelab-vm-provisioner-api/README.md`
- `homelab-vm-provisioner-client/README.md`
