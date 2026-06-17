# Homelab VM Provisioner Proxy

Dead-simple reverse proxy for the homelab VM provisioner monorepo.

## Purpose

This proxy serves as the single entry point for the application, handling:
- Static file serving for the React client
- Proxying API requests to the backend

## Architecture Role

```
Browser → Proxy (port 3000) → API (port 3001) → Python CLI → libvirt
         ↓
      Static Files (React app)
```

## Configuration

Environment variables:
- `PORT`: Proxy server port (default: `3000`)
- `API_URL`: Backend API URL (default: `http://localhost:3001`)

## Testing

None - this is intentionally a dead-simple proxy with no tests. It uses standard Express and http-proxy-middleware patterns.

## Development

For local development with hot-reload:
1. Start the API: `cd ../homelab-vm-provisioner-api && PORT=3001 npm start`
2. Start the client dev server: `cd ../homelab-vm-provisioner-client && npm run dev`
3. Access at `http://localhost:5173` (Vite dev server proxies to API)

For production mode:
1. Build the workspace: `cd .. && ./build`
2. Start the stack: `cd .. && ./start` (or `./start --docker` for Docker mode)
3. Access at `http://localhost:3000`

## Docker Deployment

```bash
# From workspace root
./start --docker

# Or build and run proxy container manually
./scripts/build-proxy-docker
./scripts/start-proxy-docker
```

See [DOCKER.md](DOCKER.md) for full details.

## Code Style

- ES modules
- Minimal dependencies (express, http-proxy-middleware)
- No build step
- No tests
- Simple and maintainable

## Monorepo Context

This is one component of the homelab-vm-provisioner-webapp monorepo. See the root AGENTS.md and README.md for the full architecture.
