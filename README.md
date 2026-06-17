# homelab-vm-provisioner-webapp

Top-level workspace for the homelab VM provisioner web application.

This repository wires together:

- `homelab-vm-provisioner-api/`: the Express API and nested Python provisioner integration
- `homelab-vm-provisioner-client/`: the React UI
- root scripts that install dependencies, build both apps, run tests, and deploy the client bundle into the API's `public/` directory

The sub-repositories already contain their own detailed READMEs. This document covers the root project workflow.

## Workspace Layout

```text
.
|- homelab-vm-provisioner-api/
|- homelab-vm-provisioner-client/
|- setup
|- build
`- start
```

## Requirements

- Git with submodule support
- Node.js 18+
- npm
- Python 3 with `venv`
- A Linux libvirt host if you want actual VM lifecycle operations to work end-to-end
- `sudo` access for the account running the API

Notes:

- Initialize submodules before the first root setup run.
- The full system-package install path is intended for supported Linux distributions through the nested provisioner setup.
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

If you are cloning fresh, `git clone --recurse-submodules <repo>` is the simplest starting point.

What it does:

- sets up the nested Python provisioner virtual environment
- installs API npm dependencies
- installs client npm dependencies
- installs Playwright Chromium for client e2e tests
- runs the root `./build` script

If your machine already has the required OS packages, use:

```bash
./setup --skip-system-packages
```

Useful pass-through options supported by the nested provisioner setup:

- `--skip-system-packages`
- `--dev`
- `--python <binary>`

Example:

```bash
./setup --skip-system-packages --python python3
```

## Configuration

The root project mostly works out of the box. The main configuration points are the root scripts and a small set of environment variables.

### Root scripts

- `./setup`: installs dependencies and runs a full workspace build
- `./build`: runs API build tasks, client build tasks, client e2e tests, and copies `homelab-vm-provisioner-client/dist/` into `homelab-vm-provisioner-api/public/`
- `./start`: starts the API and serves the already-built client from the API

### Environment variables

| Variable | Default | Used by | Purpose |
| --- | --- | --- | --- |
| `PROVISIONER_VENV_DIR` | `homelab-vm-provisioner-api/homelab-vm-provisioner/.venv` | `./setup`, `./start` | Location of the nested Python provisioner virtual environment |
| `PORT` | `3000` | API via `./start` | HTTP port for the bundled app |
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

- `homelab-vm-provisioner-api/public/`: deployed client bundle served by the API
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

After `./setup` completes, start the workspace from the root with:

```bash
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
