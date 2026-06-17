# Homelab VM Provisioner Proxy

Dead-simple reverse proxy that serves the React client and proxies API requests to the backend.

## Architecture

```
Browser → Proxy (port 3000) → API (port 3001) → Python CLI → libvirt
         ↓
      Static Files (React app)
```

## Configuration

Environment variables:

- `PORT` - Proxy server port (default: `3000`)
- `API_URL` - Backend API URL (default: `http://localhost:3001`)

## Usage

```bash
# Install dependencies
npm install

# Start proxy (assumes API is running on port 3001)
npm start

# Start with custom configuration
PORT=8080 API_URL=http://api.example.com npm start
```

## Endpoints

- `GET /proxy-health` - Proxy health check
- `GET /health` - Proxied to backend API
- `/api/*` - Proxied to backend API
- `/*` - Served from static files (React SPA)

## Directory Structure

```
homelab-vm-provisioner-proxy/
├── package.json
├── README.md
├── src/
│   └── server.js      # Main proxy server
└── public/            # Static files (created by build)
```

## Development

The proxy is intentionally minimal - no tests, no build step, just a straightforward Express server with http-proxy-middleware.

For local development:
1. Start the API on port 3001: `cd ../homelab-vm-provisioner-api && PORT=3001 npm start`
2. Start the proxy: `npm start`
3. Access the app at `http://localhost:3000`
