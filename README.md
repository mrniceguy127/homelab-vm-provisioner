# Homelab VM Provisioner

A lightweight KVM/libvirt provisioning tool for creating and managing virtual machines using cloud-init.
<br>
[Docs & Test Coverage Reporting](https://mrniceguy127.github.io/homelab-vm-provisioner)

## Features

- KVM/libvirt VM provisioning
- Cloud-init based first-boot configuration
- Automatic administrator account creation
- Per-VM administrator SSH key generation
- User SSH key injection
- NAT and bridge networking
- Automatic subnet allocation
- Custom subnet support
- Firewalld integration
- Port forwarding support
- YAML-based configuration
- Trusted and isolated VM modes

## Quick Start

Run these commands on the libvirt host.

1. Run setup:

```bash
./setup
```

2. Create a user SSH key if you do not already have one:

```bash
mkdir -p vm/keys/users
ssh-keygen -t ed25519 -f vm/keys/users/devbox
```

3. Optional: adjust the default local data paths or guest image defaults in `vmctl.yaml`.

4. Copy the example config and adjust the VM name, username, and SSH key path:

```bash
cp configs/template.yaml.example configs/devbox.yaml
nano configs/devbox.yaml
```

5. Create the VM:

```bash
./vmctl create configs/devbox.yaml
```

6. Connect as the admin user after the VM comes up:

```bash
./vmssh-admin devbox
```

If your config forwards guest port `22`, tenant access will look like this:

```bash
ssh myuser@HOST_IP -p 2222
```

## Typical Usage

- For a third-party tenant, the value you need before provisioning is normally their public SSH key.
- When possible, add the tenant's public key before `./vmctl create` so the tenant can sign in immediately after the VM comes up.
- If the tenant public key is not available yet, you can omit `ssh_key_file` during creation. The tenant account is still created, and an administrator can add the public key later over the admin SSH path.

Typical flow when you already have the tenant public key and using default key paths:

```bash
mkdir -p vm/keys/users
cp /path/to/tenant.pub vm/keys/users/devbox.pub

nano configs/devbox.yaml

./vmctl create configs/devbox.yaml
```

If you do not have the tenant public key yet:

```bash
nano configs/devbox.yaml
# omit ssh_key_file for now

./vmctl create configs/devbox.yaml
./vmssh-admin devbox
```

---

# Project Structure

```text
ROOT
├── setup
├── test
├── vmctl
├── vmssh-admin
├── vmctl.yaml
├── scripts/
│   ├── test
│   ├── coverage
│   ├── docs-build
│   └── lint
├── pyproject.toml
├── .github/
│   └── workflows/
│       └── ci-cd.yml
│
├── homelab_vm_provisioner/
│   ├── __main__.py
│   ├── cli.py
│   ├── config.py
│   ├── constants.py
│   ├── firewall.py
│   ├── network.py
│   ├── provision.py
│   ├── system.py
│   └── templates/
│       ├── base-user-data.yaml.j2
│       └── meta-data.yaml.j2
│
├── docs/
│   ├── conf.py
│   ├── index.rst
│   ├── getting-started.rst
│   ├── architecture.rst
│   └── api/
│       └── *.rst
│
├── tests/
│   ├── test_cli.py
│   ├── test_config.py
│   ├── test_firewall.py
│   ├── test_integration.py
│   ├── test_network.py
│   └── test_provision.py
│
├── configs/
│   ├── template.yaml.example
│   └── *.yaml
│
├── vm/
│   ├── data/
│   │   └── <vm>/
│   │       ├── user-data
│   │       └── meta-data
│   ├── state/
│   │   └── <vm>.yaml
│   └── keys/
│       ├── admin/
│       │   ├── <vm>_admin_ed25519
│       │   └── <vm>_admin_ed25519.pub
│       └── users/
│           └── *.pub
│
├── .build/
│   └── coverage/
│       ├── .coverage
│       ├── coverage.xml
│       └── html/
│
└── README.md
```

## Directory Reference

| Path | Purpose |
|--------|--------|
| test | Full local verification runner |
| vmctl | CLI launcher |
| vmssh-admin | Admin SSH launcher |
| vmctl.yaml | Global default path configuration |
| setup | Project setup script |
| scripts/lint | Ruff lint runner |
| scripts/test | Python test suite runner |
| scripts/coverage | Coverage runner for the Python test suite |
| scripts/docs-build | Sphinx HTML builder |
| pyproject.toml | Project metadata and tool configuration |
| .github/workflows | CI/CD automation |
| homelab_vm_provisioner | Main Python package |
| docs | Sphinx documentation source |
| tests | Unit and integration tests |
| configs | VM definitions |
| vm | Default VM data, state, and key directories |
| .build | Coverage artifacts |
| README.md | Documentation |

---

# Installation

```bash
./setup
```

After that, use `./vmctl` and `./vmssh-admin` directly.

Python-only setup:

```bash
./setup --skip-system-packages
```

Supported host distros: Debian/Ubuntu, Fedora, RHEL/Rocky/AlmaLinux, Arch Linux.

Default guest image: Debian 12 cloud image. The default libvirt `os_variant` is `generic` for wider host compatibility. You can override the guest image URL, cached filename, and `os_variant` globally or per VM.

---

# Usage

## Start from the example config

```bash
cp configs/template.yaml.example configs/my-vm.yaml
```

## Create a VM

```bash
./vmctl create configs/devbox.yaml
```

## Destroy a VM

```bash
./vmctl destroy devbox
```

This removes the VM, attached libvirt storage, VM-specific libvirt network, VM-specific firewalld state, generated files under the VM data directory, generated admin keys, and the tracked state file.

## SSH to a VM as administrator

```bash
./vmssh-admin devbox
```

Run it on the libvirt host. The helper uses the generated key path tracked in `vm/state/` and asks libvirt for the VM's current IP. If IP discovery is unavailable, you can override it:

```bash
./vmssh-admin devbox --ip 192.168.1.50
```

---

# Development

Install dev tools:

```bash
./setup --dev
```

## Global Config

Project-wide default data paths live in `vmctl.yaml`:

```yaml
paths:
  vm_data_dir: vm/data
  vm_state_dir: vm/state
  user_key_dir: vm/keys/users
  admin_key_dir: vm/keys/admin

image:
  name: debian-12-generic-amd64.qcow2
  url: https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-amd64.qcow2
  os_variant: generic
```

These defaults are used for:

- rendered local VM artifacts in `vm/data/<vm>/`
- persisted teardown state in `vm/state/<vm>.yaml`
- user public key lookup in `vm/keys/users/`
- generated admin keypairs in `vm/keys/admin/`
- guest image download URL, cached filename, and libvirt OS variant

CI/CD:

- runs lint plus Python unit and integration tests across Python 3.10, 3.11, and 3.12
- enforces a coverage threshold
- builds the Sphinx docs
- uploads the HTML coverage artifact from the coverage job
- publishes docs to the `gh-pages` branch on `main`
- publishes the HTML coverage site to `gh-pages/coverage/` on `main`

## Run full local verification

```bash
./test
```

This runs lint, the Python test suite, and coverage.

## Build docs

```bash
./scripts/docs-build
```

HTML output:

```text
docs/_build/html/
```

## Run Python tests

```bash
./scripts/test
```

This discovers both unit and integration tests under `tests/`.

## Integration tests

Integration coverage lives in `tests/test_integration.py`.

It exercises `create`, `destroy`, and `ssh-admin` through `cli.main()` while faking host-side commands such as `virsh`, `ssh`, image creation, and firewalld calls.

## Run coverage

```bash
./scripts/coverage
```

This runs the same Python test suite under coverage.

HTML output:

```text
.build/coverage/html/index.html
```

XML output:

```text
.build/coverage/coverage.xml
```

## Run linting

Run it with the repo helper:

```bash
./scripts/lint
```

Or directly:

```bash
.venv/bin/python -m ruff check homelab_vm_provisioner tests
```

---

# Configuration Overview

```yaml
vm:
  name: devbox
  user: matt
  ssh_key_file: matt.pub

network:
  mode: nat-auto

packages:
  - git

ports:
  - host: 2222
    guest: 22
```

---

# Configuration Reference

## Top-Level Sections

| Section | Required | Default | Description |
|----------|----------|----------|----------|
| vm | Yes | N/A | VM settings |
| paths | No | {} | Local artifact directory overrides |
| image | No | {} | Guest cloud image and libvirt distro settings |
| network | No | nat-auto | Networking configuration |
| packages | No | [] | Packages installed during first boot |
| ports | No | [] | NAT port forwarding rules |

## vm Section

### Example

```yaml
vm:
  name: devbox
  user: matt
  ssh_key_file: matt.pub

  ram_mb: 8192
  vcpus: 4
  disk_gb: 80

  allow_sudo: true
  trust: trusted
  template: base
```

### Fields

| Field | Required | Type | Default | Description |
|----------|----------|----------|----------|----------|
| name | Yes | string | N/A | VM name. Keep it at 12 characters or fewer |
| user | Yes | string | N/A | User account created inside VM |
| ssh_key_file | No | string | None | Tenant public key. Bare filenames resolve under `vm/keys/users/` by default |
| ram_mb | Yes | integer | N/A | Memory allocation |
| vcpus | Yes | integer | N/A | Virtual CPUs |
| disk_gb | Yes | integer | N/A | Disk size |
| allow_sudo | No | bool | false | Passwordless sudo |
| trust | No | string | untrusted | trusted/untrusted |
| template | No | string | base | Cloud-init template |

### Trust Values

| Value | Description |
|----------|----------|
| trusted | Full network access |
| untrusted | Private networks blocked |

Notes:

- `vm.name` is limited to 12 characters so the default firewalld zone name `<vm>-zone` stays within firewalld's 17-character limit.

## paths Section

### Example

```yaml
paths:
  vm_data_dir: vm/data/devbox
```

### Fields

| Field | Required | Type | Default | Description |
|----------|----------|----------|----------|----------|
| vm_data_dir | No | string | `vm/data/<vm-name>` | Local directory for rendered cloud-init files and other per-VM local artifacts |

Notes:

- Relative `vm_data_dir` values resolve from the project root.
- Persisted state is always stored separately in `vm/state/<vm-name>.yaml`.
- Global defaults for VM data, state, and key directories come from `vmctl.yaml`.

## image Section

### Example

```yaml
image:
  url: https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
  os_variant: ubuntu24.04
  name: noble-server-cloudimg-amd64.img
```

### Fields

| Field | Required | Type | Default | Description |
|----------|----------|----------|----------|----------|
| url | No | string | Debian 12 cloud image URL from `vmctl.yaml` | Download URL for the guest cloud image |
| os_variant | No | string | `generic` | Libvirt OS variant passed to `virt-install` |
| name | No | string | Derived from `url` when overridden, otherwise from `vmctl.yaml` | Local cached filename under the image directory |

Notes:

- Global image defaults live in `vmctl.yaml` under `image:`.
- Per-VM `image:` settings override the global image settings.
- If you override `url` and omit `name`, the cached filename is derived from the URL.
- `generic` is the default because some hosts do not recognize newer distro-specific IDs like `debian12`.
- Use `virt-install --osinfo list` on the host to discover supported distro-specific `os_variant` values.

---

# Network Configuration

## Default

If omitted:

```yaml
network:
  mode: nat-auto
```

### Supported Modes

| Mode | Description |
|----------|----------|
| nat-auto | Automatically selects an unused subnet |
| nat-custom | Uses a custom subnet |
| bridge | Connects VM directly to LAN |

## NAT Auto

```yaml
network:
  mode: nat-auto
```

Generated values:

| Setting | Example |
|----------|----------|
| Subnet | 192.168.137.0/24 |
| Gateway | 192.168.137.1 |
| VM IP | 192.168.137.50 |
| DHCP Start | 192.168.137.50 |
| DHCP End | 192.168.137.99 |
| Network Name | `<vm>-net` |
| Firewall Zone | `<vm>-zone` |
| MAC Address | Random |

## NAT Custom

```yaml
network:
  mode: nat-custom
  subnet_prefix: 192.168.240
```

Generated defaults:

| Setting | Value |
|----------|----------|
| cidr | 192.168.240.0/24 |
| gateway | 192.168.240.1 |
| vm_ip | 192.168.240.50 |
| dhcp_start | 192.168.240.50 |
| dhcp_end | 192.168.240.99 |

### Fields

| Field | Required | Default |
|----------|----------|----------|
| mode | Yes | N/A |
| subnet_prefix | No | None |
| cidr | Conditional | Generated |
| gateway | Conditional | Generated |
| vm_ip | Conditional | Generated |
| dhcp_start | Conditional | Generated |
| dhcp_end | Conditional | Generated |
| name | No | `<vm>-net` |
| zone | No | `<vm>-zone` |
| mac | No | Random |

## Bridge

```yaml
network:
  mode: bridge
  bridge_name: br0
```

### Fields

| Field | Required | Default |
|----------|----------|----------|
| mode | Yes | N/A |
| bridge_name | No | br0 |
| mac | No | Random |

Notes:

- VM receives DHCP from router
- VM appears as a normal LAN device
- Recommended only for trusted workloads

---

# Packages

Default:

```yaml
packages: []
```

Example:

```yaml
packages:
  - git
  - tmux
  - htop
```

---

# Port Forwarding

Default:

```yaml
ports: []
```

Example:

```yaml
ports:
  - host: 2222
    guest: 22

  - host: 8080
    guest: 80
```

| Field | Required | Default |
|----------|----------|----------|
| host | Yes | N/A |
| guest | Yes | N/A |
| proto | No | tcp |

---

# Administrator Account

Automatically created for every VM.

| Property | Value |
|----------|----------|
| Username | vmadmin |
| Password Login | Disabled |
| SSH Login | Enabled |
| Sudo Access | Full |
| Key Type | Per-VM ED25519 |

Generated files:

```text
vm/keys/admin/
├── devbox_admin_ed25519
└── devbox_admin_ed25519.pub
```

Rendered `user-data` and `meta-data` files for the VM are stored under `vm/data/devbox/` by default.

Tenant public keys are optional at create time. If `ssh_key_file` is omitted, the tenant user is still created and an administrator can add the tenant key later.

---

# Example Configurations

## Development Workstation

```yaml
vm:
  name: devbox
  user: matt
  ssh_key_file: matt.pub

  ram_mb: 8192
  vcpus: 4
  disk_gb: 80

  allow_sudo: true
  trust: trusted

network:
  mode: bridge
  bridge_name: br0

packages:
  - git
  - tmux
  - htop
```

## Web Service

```yaml
vm:
  name: web-service
  user: deploy
  ssh_key_file: deploy.pub

  ram_mb: 4096
  vcpus: 2
  disk_gb: 40

network:
  mode: nat-auto

packages:
  - nginx

ports:
  - host: 8080
    guest: 80
```

## Container Host

```yaml
vm:
  name: container-host
  user: operator
  ssh_key_file: operator.pub

  ram_mb: 6144
  vcpus: 4
  disk_gb: 80

  allow_sudo: true

network:
  mode: nat-auto

packages:
  - docker.io

ports:
  - host: 2222
    guest: 22
```

---

