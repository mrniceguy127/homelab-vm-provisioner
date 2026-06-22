import express from 'express';
import { fileURLToPath } from 'node:url';
import { createRepository } from './repository.js';

const PORT = Number.parseInt(process.env.DB_SERVICE_PORT || '3002', 10);
const hasModularDatabaseConfig = [
  'POSTGRES_HOST',
  'POSTGRES_PORT',
  'POSTGRES_USER',
  'POSTGRES_PASSWORD',
  'POSTGRES_DB',
].some((key) => Object.hasOwn(process.env, key));

function resolveDatabaseUrl() {
  if (!hasModularDatabaseConfig && process.env.DATABASE_URL) {
    return process.env.DATABASE_URL;
  }

  const host = process.env.POSTGRES_HOST || 'localhost';
  const port = process.env.POSTGRES_PORT || '5432';
  const user = encodeURIComponent(process.env.POSTGRES_USER || 'hlvmp');
  const password = encodeURIComponent(process.env.POSTGRES_PASSWORD || 'hlvmppass');
  const database = encodeURIComponent(process.env.POSTGRES_DB || 'hlvmp');

  return `postgresql://${user}:${password}@${host}:${port}/${database}`;
}

const DATABASE_URL = resolveDatabaseUrl();
const DB_SERVICE_PASSWORD = process.env.DB_SERVICE_PASSWORD || 'changeme_db_secret';

if (!DB_SERVICE_PASSWORD) {
  console.error('Error: DB_SERVICE_PASSWORD environment variable is required');
  process.exit(1);
}

export const app = express();
app.use(express.json({ limit: '10mb' }));

// Authentication middleware
function authenticate(req, res, next) {
  // Skip auth for health check
  if (req.path === '/health') {
    return next();
  }
  
  const authHeader = req.headers.authorization;
  
  if (!authHeader) {
    return res.status(401).json({ error: 'Missing Authorization header' });
  }
  
  // Support both "Bearer <password>" and just "<password>"
  const token = authHeader.startsWith('Bearer ')
    ? authHeader.slice(7)
    : authHeader;
  
  if (token !== authToken) {
    return res.status(401).json({ error: 'Invalid authorization token' });
  }
  
  next();
}

app.use(authenticate);

let repository = null;
let authToken = DB_SERVICE_PASSWORD;

export function setServerContext({ repository: nextRepository, authToken: nextAuthToken } = {}) {
  if (nextRepository !== undefined) {
    repository = nextRepository;
  }
  if (nextAuthToken !== undefined) {
    authToken = nextAuthToken;
  }
}

// Initialize repository
export async function initializeRepository() {
  try {
    repository = await createRepository(DATABASE_URL);
    console.log('Database repository initialized');
  } catch (error) {
    console.error('Failed to initialize repository:', error.message);
    process.exit(1);
  }
}

// Health check
app.get('/health', (_req, res) => {
  res.json({ ok: true, service: 'homelab-vm-provisioner-db' });
});

app.get('/users', async (_req, res, next) => {
  try {
    const users = await repository.listUsers();
    res.json({ users });
  } catch (error) {
    next(error);
  }
});

app.post('/users', async (req, res, next) => {
  try {
    const { id, username, role, created_at } = req.body;
    if (!id || !username || !role) {
      return res.status(400).json({ error: 'Missing required fields: id, username, role' });
    }

    const user = await repository.upsertUser({ id, username, role, created_at });
    res.status(201).json({ user });
  } catch (error) {
    next(error);
  }
});

app.get('/network-groups', async (_req, res, next) => {
  try {
    const networkGroups = await repository.listNetworkGroups();
    res.json({ networkGroups });
  } catch (error) {
    next(error);
  }
});

app.post('/network-groups', async (req, res, next) => {
  try {
    const {
      id,
      owner_user_id,
      name,
      libvirt_network_name,
      bridge_name,
      subnet_cidr,
      gateway_ip,
      dhcp_start,
      dhcp_end,
      profile,
      created_at,
    } = req.body;

    if (!id || !owner_user_id || !name || !profile) {
      return res.status(400).json({ error: 'Missing required fields: id, owner_user_id, name, profile' });
    }

    const networkGroup = await repository.upsertNetworkGroup({
      id,
      owner_user_id,
      name,
      libvirt_network_name,
      bridge_name,
      subnet_cidr,
      gateway_ip,
      dhcp_start,
      dhcp_end,
      profile,
      created_at,
    });

    res.status(201).json({ networkGroup });
  } catch (error) {
    next(error);
  }
});

app.get('/vm-definitions', async (_req, res, next) => {
  try {
    const vmDefinitions = await repository.listVmDefinitions();
    res.json({ vmDefinitions });
  } catch (error) {
    next(error);
  }
});

app.get('/vm-definitions/by-name/:vmName', async (req, res, next) => {
  try {
    const vmDefinition = await repository.getVmDefinitionByName(req.params.vmName);
    if (!vmDefinition) {
      return res.status(404).json({ error: 'VM definition not found' });
    }
    res.json({ vmDefinition });
  } catch (error) {
    next(error);
  }
});

app.get('/vm-definitions/:id', async (req, res, next) => {
  try {
    const vmDefinitionId = Number.parseInt(req.params.id, 10);
    if (Number.isNaN(vmDefinitionId)) {
      return res.status(400).json({ error: 'Invalid VM definition ID' });
    }
    const vmDefinition = await repository.getVmDefinitionById(vmDefinitionId);
    if (!vmDefinition) {
      return res.status(404).json({ error: 'VM definition not found' });
    }
    res.json({ vmDefinition });
  } catch (error) {
    next(error);
  }
});

app.post('/vm-definitions', async (req, res, next) => {
  try {
    const {
      vm_name,
      owner_user_id,
      network_group_id,
      target_host_id,
      config,
      ssh_public_key,
      setup_script,
    } = req.body;

    if (!vm_name || !target_host_id || !config) {
      return res.status(400).json({ error: 'Missing required fields: vm_name, target_host_id, config' });
    }

    const vmDefinition = await repository.upsertVmDefinition({
      vm_name,
      owner_user_id,
      network_group_id,
      target_host_id,
      config,
      ssh_public_key,
      setup_script,
    });

    res.status(201).json({ vmDefinition });
  } catch (error) {
    next(error);
  }
});

app.post('/vm-definition-jobs', async (req, res, next) => {
  try {
    const { vmDefinition, jobType, jobPayload, jobOptions } = req.body;
    if (!vmDefinition || !jobType || !jobPayload) {
      return res.status(400).json({ error: 'Missing required fields: vmDefinition, jobType, jobPayload' });
    }

    const result = await repository.upsertVmDefinitionAndEnqueueJob(
      vmDefinition,
      jobType,
      jobPayload,
      jobOptions || {},
    );

    res.status(201).json(result);
  } catch (error) {
    next(error);
  }
});

app.delete('/vm-definitions/by-name/:vmName', async (req, res, next) => {
  try {
    const vmDefinition = await repository.deleteVmDefinition(req.params.vmName);
    if (!vmDefinition) {
      return res.status(404).json({ error: 'VM definition not found' });
    }
    res.json({ vmDefinition });
  } catch (error) {
    next(error);
  }
});

app.get('/vm-runtime-state', async (_req, res, next) => {
  try {
    const runtimeStates = await repository.listVmRuntimeStates();
    res.json({ runtimeStates });
  } catch (error) {
    next(error);
  }
});

app.get('/vm-runtime-state/:vmName', async (req, res, next) => {
  try {
    const runtimeState = await repository.getVmRuntimeState(req.params.vmName);
    if (!runtimeState) {
      return res.status(404).json({ error: 'VM runtime state not found' });
    }
    res.json({ runtimeState });
  } catch (error) {
    next(error);
  }
});

app.post('/vm-runtime-state/:vmName', async (req, res, next) => {
  try {
    const { vmName } = req.params;
    const { state, observationSource } = req.body;
    const runtimeState = await repository.upsertVmRuntimeState(vmName, state || {}, observationSource);
    res.status(201).json({ runtimeState });
  } catch (error) {
    next(error);
  }
});

app.delete('/vm-runtime-state/:vmName', async (req, res, next) => {
  try {
    const runtimeState = await repository.deleteVmRuntimeState(req.params.vmName);
    if (!runtimeState) {
      return res.status(404).json({ error: 'VM runtime state not found' });
    }
    res.json({ runtimeState });
  } catch (error) {
    next(error);
  }
});

app.get('/vm-snapshots/:vmName', async (req, res, next) => {
  try {
    const snapshots = await repository.listVmSnapshots(req.params.vmName);
    res.json({ snapshots });
  } catch (error) {
    next(error);
  }
});

app.get('/vm-snapshots/:vmName/:snapshotId', async (req, res, next) => {
  try {
    const snapshot = await repository.getVmSnapshot(req.params.vmName, req.params.snapshotId);
    if (!snapshot) {
      return res.status(404).json({ error: 'VM snapshot not found' });
    }
    res.json({ snapshot });
  } catch (error) {
    next(error);
  }
});

app.post('/vm-snapshots/:vmName/:snapshotId', async (req, res, next) => {
  try {
    const snapshot = await repository.upsertVmSnapshot(
      req.params.vmName,
      req.params.snapshotId,
      req.body.metadata || {},
    );
    res.status(201).json({ snapshot });
  } catch (error) {
    next(error);
  }
});

app.delete('/vm-snapshots/:vmName/:snapshotId', async (req, res, next) => {
  try {
    const snapshot = await repository.deleteVmSnapshot(req.params.vmName, req.params.snapshotId);
    if (!snapshot) {
      return res.status(404).json({ error: 'VM snapshot not found' });
    }
    res.json({ snapshot });
  } catch (error) {
    next(error);
  }
});

// VM Log Snapshots
app.get('/vm-logs', async (_req, res, next) => {
  try {
    const snapshots = await repository.listVmLogSnapshots();
    res.json(snapshots);
  } catch (error) {
    next(error);
  }
});

app.get('/vm-logs/:vmName', async (req, res, next) => {
  try {
    const snapshot = await repository.getVmLogSnapshot(req.params.vmName);
    
    if (!snapshot) {
      return res.status(404).json({ 
        error: 'VM log snapshot not found',
        vm_name: req.params.vmName 
      });
    }
    
    res.json(snapshot);
  } catch (error) {
    next(error);
  }
});

app.post('/vm-logs/:vmName', async (req, res, next) => {
  try {
    const { logContent, lineCount, collectedBy } = req.body;
    
    if (!logContent || typeof lineCount !== 'number') {
      return res.status(400).json({ 
        error: 'Missing required fields: logContent, lineCount' 
      });
    }
    
    const snapshot = await repository.storeVmLogSnapshot(
      req.params.vmName,
      logContent,
      lineCount,
      collectedBy || 'worker'
    );
    
    res.json(snapshot);
  } catch (error) {
    next(error);
  }
});

app.delete('/vm-logs/:vmName', async (req, res, next) => {
  try {
    const deleted = await repository.deleteVmLogSnapshot(req.params.vmName);
    
    if (!deleted) {
      return res.status(404).json({ 
        error: 'VM log snapshot not found',
        vm_name: req.params.vmName 
      });
    }
    
    res.json({ deleted: true });
  } catch (error) {
    next(error);
  }
});

// Enqueue job
app.post('/jobs', async (req, res, next) => {
  try {
    const { type, targetHostId, targetVmId, payload, maxAttempts } = req.body;
    
    if (!type || !targetHostId || !payload) {
      return res.status(400).json({
        error: 'Missing required fields: type, targetHostId, payload',
      });
    }
    
    const job = await repository.enqueueJob(type, targetHostId, payload, {
      targetVmId,
      maxAttempts,
    });
    
    res.status(201).json({ job });
  } catch (error) {
    next(error);
  }
});

// List jobs
app.get('/jobs', async (req, res, next) => {
  try {
    const { status, targetHostId, limit } = req.query;
    
    const jobs = await repository.listJobs({
      status,
      targetHostId,
      limit: limit ? Number.parseInt(limit, 10) : undefined,
    });
    
    res.json({ jobs });
  } catch (error) {
    next(error);
  }
});

// Get job by ID
app.get('/jobs/:id', async (req, res, next) => {
  try {
    const jobId = Number.parseInt(req.params.id, 10);
    
    if (Number.isNaN(jobId)) {
      return res.status(400).json({ error: 'Invalid job ID' });
    }
    
    const job = await repository.getJob(jobId);
    
    if (!job) {
      return res.status(404).json({ error: 'Job not found' });
    }
    
    res.json({ job });
  } catch (error) {
    next(error);
  }
});

// Get job events
app.get('/jobs/:id/events', async (req, res, next) => {
  try {
    const jobId = Number.parseInt(req.params.id, 10);
    
    if (Number.isNaN(jobId)) {
      return res.status(400).json({ error: 'Invalid job ID' });
    }
    
    const limit = req.query.limit ? Number.parseInt(req.query.limit, 10) : 100;
    const events = await repository.listJobEvents(jobId, limit);
    
    res.json({ events });
  } catch (error) {
    next(error);
  }
});

// Append job event
app.post('/jobs/:id/events', async (req, res, next) => {
  try {
    const jobId = Number.parseInt(req.params.id, 10);
    
    if (Number.isNaN(jobId)) {
      return res.status(400).json({ error: 'Invalid job ID' });
    }
    
    const { level, message, metadata } = req.body;
    
    if (!level || !message) {
      return res.status(400).json({
        error: 'Missing required fields: level, message',
      });
    }
    
    const event = await repository.appendJobEvent(jobId, level, message, metadata);
    
    res.status(201).json({ event });
  } catch (error) {
    next(error);
  }
});

// Cancel job
app.post('/jobs/:id/cancel', async (req, res, next) => {
  try {
    const jobId = Number.parseInt(req.params.id, 10);
    
    if (Number.isNaN(jobId)) {
      return res.status(400).json({ error: 'Invalid job ID' });
    }
    
    const job = await repository.cancelQueuedJob(jobId);
    res.json({ job });
  } catch (error) {
    if (error.message.includes('not found') || error.message.includes('not in queued state')) {
      return res.status(400).json({ error: error.message });
    }
    next(error);
  }
});

// Claim next job for host
app.post('/jobs/claim', async (req, res, next) => {
  try {
    const { targetHostId, workerId } = req.body;
    
    if (!targetHostId || !workerId) {
      return res.status(400).json({
        error: 'Missing required fields: targetHostId, workerId',
      });
    }
    
    const job = await repository.claimNextJobForHost(targetHostId, workerId);
    
    if (!job) {
      return res.status(404).json({ error: 'No jobs available' });
    }
    
    res.json({ job });
  } catch (error) {
    next(error);
  }
});

// Mark job running
app.post('/jobs/:id/running', async (req, res, next) => {
  try {
    const jobId = Number.parseInt(req.params.id, 10);
    
    if (Number.isNaN(jobId)) {
      return res.status(400).json({ error: 'Invalid job ID' });
    }
    
    const { workerId } = req.body;
    
    if (!workerId) {
      return res.status(400).json({ error: 'Missing required field: workerId' });
    }
    
    const job = await repository.markJobRunning(jobId, workerId);
    res.json({ job });
  } catch (error) {
    next(error);
  }
});

// Mark job succeeded
app.post('/jobs/:id/succeeded', async (req, res, next) => {
  try {
    const jobId = Number.parseInt(req.params.id, 10);
    
    if (Number.isNaN(jobId)) {
      return res.status(400).json({ error: 'Invalid job ID' });
    }
    
    const { result } = req.body;
    
    const job = await repository.markJobSucceeded(jobId, result || {});
    res.json({ job });
  } catch (error) {
    next(error);
  }
});

// Mark job failed
app.post('/jobs/:id/failed', async (req, res, next) => {
  try {
    const jobId = Number.parseInt(req.params.id, 10);
    
    if (Number.isNaN(jobId)) {
      return res.status(400).json({ error: 'Invalid job ID' });
    }
    
    const { error, retriable } = req.body;
    
    if (!error) {
      return res.status(400).json({ error: 'Missing required field: error' });
    }
    
    const job = await repository.markJobFailed(jobId, error, retriable || false);
    res.json({ job });
  } catch (err) {
    next(err);
  }
});

// Acquire resource locks
app.post('/locks/acquire', async (req, res, next) => {
  try {
    const { jobId, workerId, lockKeys, ttlMs } = req.body;
    
    if (!jobId || !workerId || !lockKeys || !Array.isArray(lockKeys)) {
      return res.status(400).json({
        error: 'Missing or invalid required fields: jobId, workerId, lockKeys (array)',
      });
    }
    
    const acquired = await repository.acquireResourceLocks(
      jobId,
      workerId,
      lockKeys,
      ttlMs || 300000
    );
    
    res.json({ acquired });
  } catch (error) {
    next(error);
  }
});

// Release resource locks
app.post('/locks/release', async (req, res, next) => {
  try {
    const { jobId, workerId } = req.body;
    
    if (!jobId) {
      return res.status(400).json({ error: 'Missing required field: jobId' });
    }
    
    const count = await repository.releaseResourceLocks(jobId, workerId);
    res.json({ released: count });
  } catch (error) {
    next(error);
  }
});

// Cleanup expired locks
app.post('/locks/cleanup', async (req, res, next) => {
  try {
    const count = await repository.cleanupExpiredLocks();
    res.json({ cleaned: count });
  } catch (error) {
    next(error);
  }
});

// 404 handler
app.use((_req, res) => {
  res.status(404).json({ error: 'Route not found' });
});

// Error handler
app.use((error, _req, res, _next) => {
  console.error('Error:', error);
  res.status(error.statusCode || 500).json({
    error: error.message || 'Internal server error',
  });
});

// Start server
async function main() {
  await initializeRepository();
  
  app.listen(PORT, () => {
    console.log(`homelab-vm-provisioner-db microservice listening on port ${PORT}`);
  });
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.error('Failed to start server:', error);
    process.exit(1);
  });
}
