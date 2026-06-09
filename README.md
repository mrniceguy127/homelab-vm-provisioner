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

---

# Project Structure

```text
homelab-vm/
├── vmctl
├── vmssh-admin
├── vmctl.py
├── configs/
│   ├── devbox.yaml
│   ├── web-service.yaml
│   └── isolated-node.yaml
│
├── templates/
│   ├── base-user-data.yaml.j2
│   └── meta-data.yaml.j2
│
├── keys/
│   ├── matt.pub
│   └── deploy.pub
│
├── provider-keys/
│   ├── devbox_provider_ed25519
│   ├── devbox_provider_ed25519.pub
│   └── ...
│
├── .build/
│   ├── devbox/
│   │   ├── user-data
│   │   └── meta-data
│   └── ...
│
└── README.md
```

## Directory Reference

| Path | Purpose |
|--------|--------|
| vmctl | CLI launcher |
| vmssh-admin | Admin SSH launcher |
| vmctl.py | Main provisioning application |
| configs | VM definitions |
| templates | Cloud-init templates |
| keys | User public keys |
| provider-keys | Generated administrator keypairs |
| .build | Generated cloud-init artifacts |
| README.md | Documentation |

---

# Installation

## Debian / Ubuntu

```bash
sudo apt update

sudo apt install -y \
    libvirt-daemon-system \
    virtinst \
    qemu-utils \
    cloud-image-utils \
    firewalld \
    wget \
    openssh-client \
    python3-yaml \
    python3-jinja2
```

Enable services:

```bash
sudo systemctl enable --now libvirtd
sudo systemctl enable --now firewalld
```

## Fedora

```bash
sudo dnf install -y \
    qemu-kvm \
    libvirt \
    virt-install \
    qemu-img \
    cloud-utils \
    firewalld \
    wget \
    openssh-clients \
    python3-PyYAML \
    python3-jinja2
```

Enable services:

```bash
sudo systemctl enable --now libvirtd
sudo systemctl enable --now firewalld
```

## RHEL / Rocky / AlmaLinux

`cloud-utils` is in EPEL on some releases.

```bash
sudo dnf install -y epel-release

sudo dnf install -y \
    qemu-kvm \
    libvirt \
    virt-install \
    qemu-img \
    cloud-utils \
    firewalld \
    wget \
    openssh-clients \
    python3-PyYAML \
    python3-jinja2
```

Enable services:

```bash
sudo systemctl enable --now libvirtd
sudo systemctl enable --now firewalld
```

## Arch Linux

```bash
sudo pacman -Syu --needed \
    qemu-full \
    libvirt \
    virt-install \
    cloud-image-utils \
    firewalld \
    wget \
    openssh \
    python-pyyaml \
    python-jinja
```

Enable services:

```bash
sudo systemctl enable --now libvirtd.service
sudo systemctl enable --now firewalld.service
```

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

## SSH to a VM as administrator

```bash
./vmssh-admin devbox
```

Run it on the libvirt host. The helper uses the generated key in `provider-keys/` and asks libvirt for the VM's current IP. If IP discovery is unavailable, you can override it:

```bash
./vmssh-admin devbox --ip 192.168.1.50
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
