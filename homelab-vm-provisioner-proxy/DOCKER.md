# Docker Deployment for Reverse Proxy

Runtime Docker container for the homelab VM provisioner reverse proxy.

## Overview

The proxy container:
- Runs Express server with http-proxy-middleware
- Serves static React files from `/app/public`
- Proxies `/api/*` and `/health` to backend API
- Exposes port 3000
- Includes health check endpoint

## Quick Start

### Option 1: Helper Scripts (Recommended)

```bash
# Build client static files (if not already done)
./scripts/build-client-docker

# Build proxy image
./scripts/build-proxy-docker

# Run proxy container (API on host at port 3001)
./scripts/start-proxy-docker
```

### Option 2: Manual Docker Commands

```bash
# Build the image
docker build -t homelab-vm-provisioner-proxy homelab-vm-provisioner-proxy/

# Run the container
docker run --rm \
  -p 3000:3000 \
  -e "API_URL=http://host.docker.internal:3001" \
  -v "$(pwd)/homelab-vm-provisioner-proxy/public:/app/public:ro" \
  homelab-vm-provisioner-proxy:latest
```

**Recommended**: Use the workspace `./start --docker` script which handles both API and proxy setup.

## Configuration

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `3000` | Proxy listen port inside container |
| `API_URL` | `http://localhost:3001` | Backend API URL |

Set via script:
```bash
PROXY_PORT=8080 API_PORT=8081 ./start --docker
```

## Volume Mounts

The proxy requires static files to be mounted:

```bash
-v /path/to/static/files:/app/public:ro
```

For the workspace setup:
```bash
-v $(pwd)/homelab-vm-provisioner-proxy/public:/app/public:ro
```

## Networking

### Accessing Host API

When the API runs on the host machine:
- **macOS/Windows**: Use `host.docker.internal:3001`
- **Linux**: Use `--network host` or configure bridge networking

Example:
```bash
docker run -e "API_URL=http://host.docker.internal:3001" ...
```

### Docker Compose

In docker-compose, services communicate via service names:
```yaml
environment:
  - API_URL=http://api:3001
```

## Health Check

The container includes a built-in health check:

```bash
# Check health from host
curl http://localhost:3000/proxy-health

# Docker health status
docker ps --filter name=hlvmp-proxy
```

## Development vs Production

### Development

Run API and client dev server on host:
```bash
# Terminal 1: API
cd homelab-vm-provisioner-api && PORT=3001 npm start

# Terminal 2: Client dev server
cd homelab-vm-provisioner-client && npm run dev
# Access at http://localhost:5173
```

### Production-like Testing

Use Docker proxy + host API:
```bash
./scripts/start-proxy-docker
# Access at http://localhost:3000
```

Or use the workspace start script for easier management:
```bash
cd .. && ./start --docker
```

## Image Details

- **Base**: node:18-alpine
- **Size**: ~200MB (minimal Alpine base + Express + proxy middleware)
- **User**: node (non-root)
- **Exposed Port**: 3000
- **Health Check**: GET /proxy-health every 30s

## Logs

View container logs:

```bash
# Follow logs
docker logs -f hlvmp-proxy
```

## Troubleshooting

### Static files not found

**Error**: 404 on all routes

**Solution**: Ensure static files are mounted:
```bash
ls -la homelab-vm-provisioner-proxy/public/
# Should contain index.html and assets/
./scripts/build-client-docker
```

### Cannot reach API

**Error**: 502 Bad Gateway

**Solutions**:
1. Check API is running: `curl http://localhost:3001/health`
2. Verify API_URL: `docker logs hlvmp-proxy | grep "Proxying API requests"`
3. On Linux, try `--network host` or use bridge network

### Port already in use

**Error**: Bind for 0.0.0.0:3000 failed: port is already allocated

**Solution**: Change port:
```bash
PROXY_PORT=8080 ./scripts/start-proxy-docker
# Or: docker run -p 8080:3000 ...
```

## Security Notes

- Static files mounted read-only (`:ro`)
- Container runs as non-root user
- No sensitive data in environment variables
- Health check uses localhost only
- CORS handled by API, not proxy
