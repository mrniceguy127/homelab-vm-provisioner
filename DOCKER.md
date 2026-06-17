# Docker Mode Guide

Complete guide for running the homelab VM provisioner with Docker.

## Overview

Docker mode runs the reverse proxy in a container while keeping the API on the host (needed for libvirt access).

```
Browser → Proxy Container (3000) → API Host (3001) → Python CLI → libvirt
         ↓
      Static Files (volume mount)
```

## Quick Start

```bash
# One-time setup
./setup --docker

# Start services
./start --docker

# Access at http://localhost:3000
```

## Detailed Setup

### 1. Install Prerequisites

**Required (your responsibility to install):**
- **Docker Desktop** (macOS/Windows) or **Docker Engine** (Linux)
  - The setup scripts do NOT install Docker for you
  - Install Docker before running `./setup --docker`
- Git (for cloning and submodule management)

**Installed by component setup scripts:**
- Python 3 with venv (for API/provisioner)
- Node.js 18+ (for API)

### 2. Clone and Initialize

```bash
git clone --recurse-submodules <repo-url>
cd homelab-vm-provisioner
```

### 3. Setup for Docker Mode

```bash
./setup --docker
```

This will:
- Initialize Python provisioner
- Install API dependencies
- Install client dependencies (for tests)
- Skip proxy npm install (runs in Docker)
- Build client with Docker
- Build proxy Docker image

### 4. Start Services

```bash
./start --docker
```

This runs:
- API on host (port 3001)
- Proxy in Docker (port 3000)

## Configuration

### Environment Variables

```bash
# Custom ports
PROXY_PORT=8080 API_PORT=8081 ./start --docker

# Python environment
PROVISIONER_VENV_DIR=/custom/path ./setup --docker
```

### Port Mapping

| Service | Default | Environment Variable |
|---------|---------|---------------------|
| Proxy   | 3000    | `PROXY_PORT`        |
| API     | 3001    | `API_PORT`          |

## Docker Images

### Client Builder (`homelab-vm-provisioner-client-builder`)
- Purpose: Build static files
- Size: ~334MB
- Type: Build-time only (ephemeral containers)
- Usage: `./homelab-vm-provisioner-client/build`

### Proxy (`homelab-vm-provisioner-proxy`)
- Purpose: Runtime reverse proxy
- Size: ~133MB
- Type: Runtime service
- Usage: `./start --docker` or `./homelab-vm-provisioner-proxy/start`

### Rebuild Images

```bash
# Rebuild client builder image
docker rmi homelab-vm-provisioner-client-builder
./homelab-vm-provisioner-client/build

# Rebuild proxy image
docker rmi homelab-vm-provisioner-proxy
./homelab-vm-provisioner-proxy/build
```

## Manual Operations

### Build Client Static Files

```bash
./homelab-vm-provisioner-client/build
# Output: homelab-vm-provisioner-proxy/public/
```

### Build Proxy Image

```bash
./homelab-vm-provisioner-proxy/build
```

### Run Proxy Container Manually

```bash
docker run --rm \
  --name hlvmp-proxy \
  -p 3000:3000 \
  -e "API_URL=http://host.docker.internal:3001" \
  -v "$(pwd)/homelab-vm-provisioner-proxy/public:/app/public:ro" \
  homelab-vm-provisioner-proxy:latest
```

## Development Workflows

### Local Development (Recommended)

Use local servers with hot reload:

```bash
# Terminal 1: API
cd homelab-vm-provisioner-api && PORT=3001 npm start

# Terminal 2: Client dev server
cd homelab-vm-provisioner-client && npm run dev

# Access at http://localhost:5173
```

### Docker Mode (Proxy + Local API)

Test production-like setup with Docker proxy and local API:

```bash
./start --docker
# Access at http://localhost:3000
```

Configure ports with environment variables:

```bash
PROXY_PORT=8080 API_PORT=8081 ./start --docker
```

## Troubleshooting

### Port Conflicts

```bash
# Check what's using ports
lsof -i :3000
lsof -i :3001

# Use different ports
PROXY_PORT=8080 API_PORT=8081 ./start --docker
```

### Docker Image Not Found

```bash
# Build images
./homelab-vm-provisioner-client/build
./homelab-vm-provisioner-proxy/build

# Or run setup again
./setup --docker
```

### Static Files Not Found

```bash
# Check if files exist
ls -la homelab-vm-provisioner-proxy/public/

# Rebuild if needed
./homelab-vm-provisioner-client/build
```

### API Cannot Connect to Proxy

On Linux, Docker's `host.docker.internal` may not work:

```bash
# Option 1: Use host mode (proxy on host network)
docker run --network host ...

# Option 2: Use bridge network with host IP
docker run -e "API_URL=http://192.168.1.x:3001" ...
```

### Permission Issues with Volume Mount

Ensure static files are readable:

```bash
chmod -R a+r homelab-vm-provisioner-proxy/public/
```

## Updating

When code changes:

```bash
# Update code
git pull
git submodule update --init --recursive

# Rebuild
./homelab-vm-provisioner-client/build  # Client changes
./homelab-vm-provisioner-proxy/build   # Proxy changes
npm --prefix homelab-vm-provisioner-api install  # API changes

# Restart
./start --docker
```

## Production Deployment

For production, consider:

1. **Reverse Proxy**: Use nginx/Traefik in front
2. **TLS**: Terminate SSL at edge
3. **API Host**: Run on libvirt host with proper privileges
4. **Image Registry**: Push images to private registry
5. **Orchestration**: Use Kubernetes/Swarm for HA
6. **Monitoring**: Add health check endpoints
7. **Logging**: Aggregate logs from containers

Example production stack:

```
Internet → nginx (TLS) → Proxy Container → API (libvirt host) → VMs
```

## Comparison: Local vs Docker Mode

| Feature | Local Mode | Docker Mode |
|---------|-----------|-------------|
| Setup | `./setup` | `./setup --docker` |
| Start | `./start` | `./start --docker` |
| Proxy | npm (host) | Docker container |
| API | npm (host) | npm (host) |
| Client Build | npm (host) | Docker (build-time) |
| Hot Reload | Yes (dev mode) | No |
| Production-like | Partial | Yes (proxy) |
| Dependencies | More (full npm) | Less (Docker images) |
| Isolation | No | Yes (proxy) |

## Summary

Docker mode provides:
- ✅ Containerized proxy (isolated, reproducible)
- ✅ Host API (full libvirt access)
- ✅ Docker client builds (consistent)
- ✅ Easy deployment
- ✅ Simplified dependencies

Use `./setup --docker` and `./start --docker` for the best Docker experience.
