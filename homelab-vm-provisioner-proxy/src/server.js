import path from 'node:path';
import { fileURLToPath } from 'node:url';

import express from 'express';
import { createProxyMiddleware } from 'http-proxy-middleware';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Configuration
const port = Number.parseInt(process.env.PORT || '3000', 10);
const apiUrl = process.env.API_URL || 'http://localhost:3001';
const staticRoot = path.resolve(__dirname, '../public');
const staticIndexPath = path.join(staticRoot, 'index.html');

const app = express();

// Health check for the proxy itself
app.get('/proxy-health', (_request, response) => {
  response.json({ status: 'ok', service: 'homelab-vm-provisioner-proxy' });
});

// Proxy configuration for API endpoints
const proxyOptions = {
  target: apiUrl,
  changeOrigin: true,
  onProxyReq: (proxyReq, request) => {
    console.log(`[Proxy] ${request.method} ${request.url} -> ${apiUrl}${request.url}`);
  },
  onError: (error, request, response) => {
    console.error(`[Proxy Error] ${request.method} ${request.url}:`, error.message);
    if (!response.headersSent) {
      response.status(502).json({
        error: 'Bad Gateway',
        message: 'Unable to reach backend API',
        details: error.message,
      });
    }
  },
};

// Proxy /health to backend API
app.use('/health', createProxyMiddleware(proxyOptions));

// Proxy all /api/* requests to backend API
app.use('/api', createProxyMiddleware(proxyOptions));

// Serve static files (built React app)
app.use(express.static(staticRoot, { index: false }));

// SPA fallback: serve index.html for all other routes
app.get('*', (request, response) => {
  response.sendFile(staticIndexPath, (error) => {
    if (error) {
      console.error(`Failed to serve ${staticIndexPath}:`, error.message);
      response.status(404).json({
        error: 'Not Found',
        message: 'Client application not found. Run ./setup to build the client.',
      });
    }
  });
});

// Start server
app.listen(port, () => {
  console.log(`homelab-vm-provisioner-proxy listening on port ${port}`);
  console.log(`Proxying API requests to: ${apiUrl}`);
  console.log(`Serving static files from: ${staticRoot}`);
});
