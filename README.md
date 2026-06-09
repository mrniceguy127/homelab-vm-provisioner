# Homelab VM Provisioner

A lightweight KVM/libvirt provisioning tool for creating and managing virtual machines using cloud-init.

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
mkdir -p keys
ssh-keygen -t ed25519 -f keys/devbox
```

3. Copy the example config and adjust the VM name, username, and SSH key path:

```bash
cp configs/template.yaml.example configs/devbox.yaml
nano configs/devbox.yaml
```

4. Create the VM:

```bash
./vmctl create configs/devbox.yaml
```

5. Connect as the admin user after the VM comes up:

```bash
./vmssh-admin devbox
```

If your config forwards guest port `22`, tenant access will look like this:

```bash
ssh myuser@HOST_IP -p 2222
```

---

# Project Structure

```text
homelab-vm/
├── setup
├── vmctl
├── vmssh-admin
├── test
├── coverage
├── lint
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
│   ├── test_network.py
│   └── test_provision.py
│
├── configs/
│   ├── template.yaml.example
│   └── *.yaml
│
├── keys/
│   └── *.pub
│
├── provider-keys/
│   └── ...
│
├── .build/
│   └── ...
│
└── README.md
```

## Directory Reference

| Path | Purpose |
|--------|--------|
| vmctl | CLI launcher |
| vmssh-admin | Admin SSH launcher |
| lint | Ruff lint runner |
| setup | Project setup script |
| test | Unit test runner |
| coverage | Coverage runner |
| pyproject.toml | Project metadata and tool configuration |
| .github/workflows | CI/CD automation |
| homelab_vm_provisioner | Main Python package |
| docs | Sphinx documentation source |
| tests | Unit tests |
| configs | VM definitions |
| keys | User public keys |
| provider-keys | Generated administrator keypairs |
| .build | Generated cloud-init artifacts |
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

Supported distros: Debian/Ubuntu, Fedora, RHEL/Rocky/AlmaLinux, Arch Linux.

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

This removes the VM, attached libvirt storage, VM-specific libvirt network, VM-specific firewalld state, generated admin keys, and generated `.build/` artifacts.

## SSH to a VM as administrator

```bash
./vmssh-admin devbox
```

Run it on the libvirt host. The helper uses the generated key in `provider-keys/` and asks libvirt for the VM's current IP. If IP discovery is unavailable, you can override it:

```bash
./vmssh-admin devbox --ip 192.168.1.50
```

---

# Development

Install dev tools:

```bash
./setup --dev
```

CI/CD:

- runs lint and tests across Python 3.10, 3.11, and 3.12
- enforces a coverage threshold
- builds the Sphinx docs
- uploads the HTML coverage artifact from the coverage job
- publishes docs to the `gh-pages` branch on `main`
- publishes the HTML coverage site to `gh-pages/coverage/` on `main`

## Build docs

```bash
make -C docs html
```

HTML output:

```text
docs/_build/html/
```

## Run unit tests

```bash
./test
```

## Run coverage

```bash
./coverage
```

HTML output:

```text
coverage-html/index.html
```

## Run linting

Run it with the repo helper:

```bash
./lint
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
  ssh_key_file: ./keys/matt.pub

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
| network | No | nat-auto | Networking configuration |
| packages | No | [] | Packages installed during first boot |
| ports | No | [] | NAT port forwarding rules |

## vm Section

### Example

```yaml
vm:
  name: devbox
  user: matt
  ssh_key_file: ./keys/matt.pub

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
| name | Yes | string | N/A | VM name |
| user | Yes | string | N/A | User account created inside VM |
| ssh_key_file | Yes | string | N/A | User public key |
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
provider-keys/
├── devbox_provider_ed25519
└── devbox_provider_ed25519.pub
```

---

# Example Configurations

## Development Workstation

```yaml
vm:
  name: devbox
  user: matt
  ssh_key_file: ./keys/matt.pub

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
  ssh_key_file: ./keys/deploy.pub

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
  ssh_key_file: ./keys/operator.pub

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

# Typical Workflow

```bash
ssh-keygen -t ed25519 -f keys/devbox

nano configs/devbox.yaml

./vmctl create configs/devbox.yaml
```

User access:

```bash
ssh matt@HOST_IP -p 2222
```

Administrator access:

```bash
./vmssh-admin devbox
```
