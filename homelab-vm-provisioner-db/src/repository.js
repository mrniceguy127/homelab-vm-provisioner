import pg from 'pg';

/**
 * Repository for job queue operations
 * 
 * Provides PostgreSQL-backed job queue with safe concurrent access.
 * Uses FOR UPDATE SKIP LOCKED for race-free job claiming.
 */

export class JobRepository {
  constructor(pool) {
    this.pool = pool;
  }
  
  /**
   * Enqueue a new job
   * 
   * @param {string} type - Job type (e.g., 'provision_vm', 'destroy_vm')
   * @param {string} targetHostId - Host where job should run
   * @param {object} payload - Job-specific parameters
   * @param {object} options - Optional job configuration
   * @param {string} options.targetVmId - Target VM identifier (nullable)
   * @param {number} options.maxAttempts - Maximum retry attempts (default: 3)
   * @returns {Promise<object>} Created job
   */
  async enqueueJob(type, targetHostId, payload, options = {}) {
    const { targetVmId = null, maxAttempts = 3 } = options;
    
    const result = await this.pool.query(
      `INSERT INTO jobs (type, target_host_id, target_vm_id, payload, max_attempts)
       VALUES ($1, $2, $3, $4, $5)
       RETURNING *`,
      [type, targetHostId, targetVmId, JSON.stringify(payload), maxAttempts]
    );
    
    return this._deserializeJob(result.rows[0]);
  }
  
  /**
   * Get job by ID
   * 
   * @param {number} jobId - Job ID
   * @returns {Promise<object|null>} Job or null if not found
   */
  async getJob(jobId) {
    const result = await this.pool.query(
      'SELECT * FROM jobs WHERE id = $1',
      [jobId]
    );
    
    return result.rows[0] ? this._deserializeJob(result.rows[0]) : null;
  }
  
  /**
   * List all jobs with optional filtering
   * 
   * @param {object} filters - Optional filters
   * @param {string} filters.status - Filter by status
   * @param {string} filters.targetHostId - Filter by target host
   * @param {number} filters.limit - Maximum number of results (default: 100)
   * @returns {Promise<Array<object>>} List of jobs
   */
  async listJobs(filters = {}) {
    const { status, targetHostId, limit = 100 } = filters;
    
    let query = 'SELECT * FROM jobs WHERE 1=1';
    const params = [];
    let paramIndex = 1;
    
    if (status) {
      query += ` AND status = $${paramIndex++}`;
      params.push(status);
    }
    
    if (targetHostId) {
      query += ` AND target_host_id = $${paramIndex++}`;
      params.push(targetHostId);
    }
    
    query += ` ORDER BY created_at DESC LIMIT $${paramIndex}`;
    params.push(limit);
    
    const result = await this.pool.query(query, params);
    return result.rows.map(row => this._deserializeJob(row));
  }
  
  /**
   * Append event to job log
   * 
   * @param {number} jobId - Job ID
   * @param {string} level - Event level ('debug', 'info', 'warning', 'error')
   * @param {string} message - Event message
   * @param {object} metadata - Optional metadata (default: null)
   * @returns {Promise<object>} Created event
   */
  async appendJobEvent(jobId, level, message, metadata = null) {
    const result = await this.pool.query(
      `INSERT INTO job_events (job_id, level, message, metadata)
       VALUES ($1, $2, $3, $4)
       RETURNING *`,
      [jobId, level, message, metadata ? JSON.stringify(metadata) : null]
    );
    
    return this._deserializeJobEvent(result.rows[0]);
  }
  
  /**
   * List events for a job
   * 
   * @param {number} jobId - Job ID
   * @param {number} limit - Maximum number of events (default: 100)
   * @returns {Promise<Array<object>>} List of events
   */
  async listJobEvents(jobId, limit = 100) {
    const result = await this.pool.query(
      `SELECT * FROM job_events
       WHERE job_id = $1
       ORDER BY created_at DESC
       LIMIT $2`,
      [jobId, limit]
    );
    
    return result.rows.map(row => this._deserializeJobEvent(row));
  }
  
  /**
   * Claim next available job for a host
   * 
   * Uses FOR UPDATE SKIP LOCKED for safe concurrent claiming.
   * Only claims jobs in 'queued' status for the specified host.
   * 
   * @param {string} targetHostId - Host ID to claim job for
   * @param {string} workerId - Worker ID claiming the job
   * @returns {Promise<object|null>} Claimed job or null if none available
   */
  async claimNextJobForHost(targetHostId, workerId) {
    const client = await this.pool.connect();
    
    try {
      await client.query('BEGIN');
      
      // Find and lock next available job
      const result = await client.query(
        `SELECT * FROM jobs
         WHERE status = 'queued'
           AND target_host_id = $1
         ORDER BY created_at
         LIMIT 1
         FOR UPDATE SKIP LOCKED`,
        [targetHostId]
      );
      
      if (result.rows.length === 0) {
        await client.query('COMMIT');
        return null;
      }
      
      const job = result.rows[0];
      
      // Update job to claimed state
      await client.query(
        `UPDATE jobs
         SET claimed_by = $1, claimed_at = NOW()
         WHERE id = $2`,
        [workerId, job.id]
      );
      
      await client.query('COMMIT');
      
      return this._deserializeJob({ ...job, claimed_by: workerId, claimed_at: new Date() });
    } catch (error) {
      await client.query('ROLLBACK');
      throw error;
    } finally {
      client.release();
    }
  }
  
  /**
   * Mark job as running
   * 
   * @param {number} jobId - Job ID
   * @param {string} workerId - Worker ID running the job
   * @returns {Promise<object>} Updated job
   */
  async markJobRunning(jobId, workerId) {
    const result = await this.pool.query(
      `UPDATE jobs
       SET status = 'running',
           started_at = NOW(),
           attempts = attempts + 1
       WHERE id = $1 AND claimed_by = $2
       RETURNING *`,
      [jobId, workerId]
    );
    
    if (result.rows.length === 0) {
      throw new Error(`Job ${jobId} not found or not claimed by ${workerId}`);
    }
    
    return this._deserializeJob(result.rows[0]);
  }
  
  /**
   * Mark job as succeeded
   * 
   * @param {number} jobId - Job ID
   * @param {object} result - Job result data
   * @returns {Promise<object>} Updated job
   */
  async markJobSucceeded(jobId, result = {}) {
    const updateResult = await this.pool.query(
      `UPDATE jobs
       SET status = 'succeeded',
           result = $1,
           finished_at = NOW()
       WHERE id = $2
       RETURNING *`,
      [JSON.stringify(result), jobId]
    );
    
    if (updateResult.rows.length === 0) {
      throw new Error(`Job ${jobId} not found`);
    }
    
    return this._deserializeJob(updateResult.rows[0]);
  }
  
  /**
   * Mark job as failed
   * 
   * @param {number} jobId - Job ID
   * @param {string} error - Error message
   * @param {boolean} retriable - Whether job can be retried (default: false)
   * @returns {Promise<object>} Updated job
   */
  async markJobFailed(jobId, error, retriable = false) {
    const client = await this.pool.connect();
    
    try {
      await client.query('BEGIN');
      
      // Get current job state
      const jobResult = await client.query(
        'SELECT attempts, max_attempts FROM jobs WHERE id = $1',
        [jobId]
      );
      
      if (jobResult.rows.length === 0) {
        throw new Error(`Job ${jobId} not found`);
      }
      
      const { attempts, max_attempts } = jobResult.rows[0];
      
      // Determine if job should be retried or marked as failed
      let status = 'failed';
      let finishedAt = new Date();
      
      if (retriable && attempts < max_attempts) {
        status = 'queued';
        finishedAt = null;
      }
      
      const updateResult = await client.query(
        `UPDATE jobs
         SET status = $1,
             error = $2,
             finished_at = $3,
             claimed_by = NULL,
             claimed_at = NULL
         WHERE id = $4
         RETURNING *`,
        [status, error, finishedAt, jobId]
      );
      
      await client.query('COMMIT');
      
      return this._deserializeJob(updateResult.rows[0]);
    } catch (err) {
      await client.query('ROLLBACK');
      throw err;
    } finally {
      client.release();
    }
  }
  
  /**
   * Cancel a queued job
   * 
   * @param {number} jobId - Job ID
   * @returns {Promise<object>} Updated job
   */
  async cancelQueuedJob(jobId) {
    const result = await this.pool.query(
      `UPDATE jobs
       SET status = 'cancelled',
           finished_at = NOW()
       WHERE id = $1 AND status = 'queued'
       RETURNING *`,
      [jobId]
    );
    
    if (result.rows.length === 0) {
      throw new Error(`Job ${jobId} not found or not in queued state`);
    }
    
    return this._deserializeJob(result.rows[0]);
  }
  
  /**
   * Acquire resource locks
   * 
   * Attempts to acquire locks for the specified resources.
   * Uses INSERT ... ON CONFLICT to ensure atomic acquisition.
   * 
   * @param {number} jobId - Job ID acquiring locks
   * @param {string} workerId - Worker ID
   * @param {Array<string>} lockKeys - Resource keys to lock
   * @param {number} ttlMs - Lock TTL in milliseconds (default: 300000 = 5 minutes)
   * @returns {Promise<boolean>} True if all locks acquired
   */
  async acquireResourceLocks(jobId, workerId, lockKeys, ttlMs = 300000) {
    const client = await this.pool.connect();
    
    try {
      await client.query('BEGIN');
      
      const expiresAt = new Date(Date.now() + ttlMs);
      
      // Try to acquire each lock
      for (const lockKey of lockKeys) {
        // Clean up expired locks first
        await client.query(
          'DELETE FROM resource_locks WHERE lock_key = $1 AND expires_at < NOW()',
          [lockKey]
        );
        
        // Try to insert lock
        const result = await client.query(
          `INSERT INTO resource_locks (lock_key, job_id, worker_id, expires_at)
           VALUES ($1, $2, $3, $4)
           ON CONFLICT (lock_key) DO NOTHING
           RETURNING *`,
          [lockKey, jobId, workerId, expiresAt]
        );
        
        if (result.rows.length === 0) {
          // Lock already held by another job
          await client.query('ROLLBACK');
          return false;
        }
      }
      
      await client.query('COMMIT');
      return true;
    } catch (error) {
      await client.query('ROLLBACK');
      throw error;
    } finally {
      client.release();
    }
  }
  
  /**
   * Release resource locks for a job
   * 
   * @param {number} jobId - Job ID
   * @param {string} workerId - Worker ID (optional, for verification)
   * @returns {Promise<number>} Number of locks released
   */
  async releaseResourceLocks(jobId, workerId = null) {
    let query = 'DELETE FROM resource_locks WHERE job_id = $1';
    const params = [jobId];
    
    if (workerId) {
      query += ' AND worker_id = $2';
      params.push(workerId);
    }
    
    query += ' RETURNING *';
    
    const result = await this.pool.query(query, params);
    return result.rows.length;
  }
  
  /**
   * Clean up expired resource locks
   * 
   * @returns {Promise<number>} Number of locks cleaned up
   */
  async cleanupExpiredLocks() {
    const result = await this.pool.query(
      'DELETE FROM resource_locks WHERE expires_at < NOW() RETURNING *'
    );
    
    return result.rows.length;
  }

  async listUsers() {
    const result = await this.pool.query('SELECT * FROM users ORDER BY created_at, id');
    return result.rows.map((row) => this._deserializeUser(row));
  }

  async upsertUser(user) {
    const result = await this.pool.query(
      `INSERT INTO users (id, username, role, created_at)
       VALUES ($1, $2, $3, COALESCE($4, NOW()))
       ON CONFLICT (id) DO UPDATE
       SET username = EXCLUDED.username,
           role = EXCLUDED.role,
           updated_at = NOW()
       RETURNING *`,
      [user.id, user.username, user.role, user.created_at || null]
    );

    return this._deserializeUser(result.rows[0]);
  }

  async listNetworkGroups() {
    const result = await this.pool.query(
      'SELECT * FROM network_groups ORDER BY created_at, id'
    );
    return result.rows.map((row) => this._deserializeNetworkGroup(row));
  }

  async upsertNetworkGroup(group) {
    const result = await this.pool.query(
      `INSERT INTO network_groups (
         id, owner_user_id, name, libvirt_network_name, bridge_name,
         subnet_cidr, gateway_ip, dhcp_start, dhcp_end, profile, created_at
       )
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, COALESCE($11, NOW()))
       ON CONFLICT (id) DO UPDATE
       SET owner_user_id = EXCLUDED.owner_user_id,
           name = EXCLUDED.name,
           libvirt_network_name = EXCLUDED.libvirt_network_name,
           bridge_name = EXCLUDED.bridge_name,
           subnet_cidr = EXCLUDED.subnet_cidr,
           gateway_ip = EXCLUDED.gateway_ip,
           dhcp_start = EXCLUDED.dhcp_start,
           dhcp_end = EXCLUDED.dhcp_end,
           profile = EXCLUDED.profile,
           updated_at = NOW()
       RETURNING *`,
      [
        group.id,
        group.owner_user_id,
        group.name,
        group.libvirt_network_name || null,
        group.bridge_name || null,
        group.subnet_cidr || null,
        group.gateway_ip || null,
        group.dhcp_start || null,
        group.dhcp_end || null,
        group.profile,
        group.created_at || null,
      ]
    );

    return this._deserializeNetworkGroup(result.rows[0]);
  }

  async upsertVmDefinition(vmDefinition) {
    const result = await this.pool.query(
      `INSERT INTO vm_definitions (
         vm_name, owner_user_id, network_group_id, target_host_id, config,
         ssh_public_key, setup_script
       )
       VALUES ($1, $2, $3, $4, $5, $6, $7)
       ON CONFLICT (vm_name) DO UPDATE
       SET owner_user_id = EXCLUDED.owner_user_id,
           network_group_id = EXCLUDED.network_group_id,
           target_host_id = EXCLUDED.target_host_id,
           config = EXCLUDED.config,
           ssh_public_key = EXCLUDED.ssh_public_key,
           setup_script = EXCLUDED.setup_script,
           updated_at = NOW()
       RETURNING *`,
      [
        vmDefinition.vm_name,
        vmDefinition.owner_user_id || null,
        vmDefinition.network_group_id || null,
        vmDefinition.target_host_id,
        JSON.stringify(vmDefinition.config),
        vmDefinition.ssh_public_key || null,
        vmDefinition.setup_script || null,
      ]
    );

    return this._deserializeVmDefinition(result.rows[0]);
  }

  async getVmDefinitionByName(vmName) {
    const result = await this.pool.query(
      'SELECT * FROM vm_definitions WHERE vm_name = $1',
      [vmName]
    );
    return result.rows[0] ? this._deserializeVmDefinition(result.rows[0]) : null;
  }

  async getVmDefinitionById(vmDefinitionId) {
    const result = await this.pool.query(
      'SELECT * FROM vm_definitions WHERE id = $1',
      [vmDefinitionId]
    );
    return result.rows[0] ? this._deserializeVmDefinition(result.rows[0]) : null;
  }

  async listVmDefinitions() {
    const result = await this.pool.query('SELECT * FROM vm_definitions ORDER BY vm_name');
    return result.rows.map((row) => this._deserializeVmDefinition(row));
  }

  async deleteVmDefinition(vmName) {
    const result = await this.pool.query(
      'DELETE FROM vm_definitions WHERE vm_name = $1 RETURNING *',
      [vmName]
    );
    return result.rows[0] ? this._deserializeVmDefinition(result.rows[0]) : null;
  }

  async upsertVmRuntimeState(vmName, state, observationSource = null) {
    const result = await this.pool.query(
      `INSERT INTO vm_runtime_state (vm_name, state, observed_at, observation_source)
       VALUES ($1, $2, NOW(), $3)
       ON CONFLICT (vm_name) DO UPDATE
       SET state = EXCLUDED.state,
           observed_at = NOW(),
           observation_source = EXCLUDED.observation_source,
           updated_at = NOW()
       RETURNING *`,
      [vmName, JSON.stringify(state || {}), observationSource]
    );

    return this._deserializeVmRuntimeState(result.rows[0]);
  }

  async getVmRuntimeState(vmName) {
    const result = await this.pool.query(
      'SELECT * FROM vm_runtime_state WHERE vm_name = $1',
      [vmName]
    );
    return result.rows[0] ? this._deserializeVmRuntimeState(result.rows[0]) : null;
  }

  async listVmRuntimeStates() {
    const result = await this.pool.query('SELECT * FROM vm_runtime_state ORDER BY vm_name');
    return result.rows.map((row) => this._deserializeVmRuntimeState(row));
  }

  async deleteVmRuntimeState(vmName) {
    const result = await this.pool.query(
      'DELETE FROM vm_runtime_state WHERE vm_name = $1 RETURNING *',
      [vmName]
    );
    return result.rows[0] ? this._deserializeVmRuntimeState(result.rows[0]) : null;
  }

  async upsertVmSnapshot(vmName, snapshotId, metadata) {
    const result = await this.pool.query(
      `INSERT INTO vm_snapshots (vm_name, snapshot_id, metadata)
       VALUES ($1, $2, $3)
       ON CONFLICT (vm_name, snapshot_id) DO UPDATE
       SET metadata = EXCLUDED.metadata,
           updated_at = NOW()
       RETURNING *`,
      [vmName, snapshotId, JSON.stringify(metadata || {})]
    );

    return this._deserializeVmSnapshot(result.rows[0]);
  }

  async getVmSnapshot(vmName, snapshotId) {
    const result = await this.pool.query(
      'SELECT * FROM vm_snapshots WHERE vm_name = $1 AND snapshot_id = $2',
      [vmName, snapshotId]
    );
    return result.rows[0] ? this._deserializeVmSnapshot(result.rows[0]) : null;
  }

  async upsertVmDefinitionAndEnqueueJob(vmDefinition, jobType, jobPayload, jobOptions = {}) {
    const {
      targetVmId = null,
      maxAttempts = 3,
      targetHostId = vmDefinition.target_host_id,
    } = jobOptions;

    const client = await this.pool.connect();
    try {
      await client.query('BEGIN');

      const vmDefinitionResult = await client.query(
        `INSERT INTO vm_definitions (
           vm_name, owner_user_id, network_group_id, target_host_id, config,
           ssh_public_key, setup_script
         )
         VALUES ($1, $2, $3, $4, $5, $6, $7)
         ON CONFLICT (vm_name) DO UPDATE
         SET owner_user_id = EXCLUDED.owner_user_id,
             network_group_id = EXCLUDED.network_group_id,
             target_host_id = EXCLUDED.target_host_id,
             config = EXCLUDED.config,
             ssh_public_key = EXCLUDED.ssh_public_key,
             setup_script = EXCLUDED.setup_script,
             updated_at = NOW()
         RETURNING *`,
        [
          vmDefinition.vm_name,
          vmDefinition.owner_user_id || null,
          vmDefinition.network_group_id || null,
          vmDefinition.target_host_id,
          JSON.stringify(vmDefinition.config),
          vmDefinition.ssh_public_key || null,
          vmDefinition.setup_script || null,
        ]
      );

      const jobResult = await client.query(
        `INSERT INTO jobs (type, target_host_id, target_vm_id, payload, max_attempts)
         VALUES ($1, $2, $3, $4, $5)
         RETURNING *`,
        [jobType, targetHostId, targetVmId, JSON.stringify(jobPayload), maxAttempts]
      );

      await client.query('COMMIT');

      return {
        vmDefinition: this._deserializeVmDefinition(vmDefinitionResult.rows[0]),
        job: this._deserializeJob(jobResult.rows[0]),
      };
    } catch (error) {
      await client.query('ROLLBACK');
      throw error;
    } finally {
      client.release();
    }
  }

  async listVmSnapshots(vmName) {
    const result = await this.pool.query(
      'SELECT * FROM vm_snapshots WHERE vm_name = $1 ORDER BY created_at DESC',
      [vmName]
    );
    return result.rows.map((row) => this._deserializeVmSnapshot(row));
  }

  async deleteVmSnapshot(vmName, snapshotId) {
    const result = await this.pool.query(
      'DELETE FROM vm_snapshots WHERE vm_name = $1 AND snapshot_id = $2 RETURNING *',
      [vmName, snapshotId]
    );
    return result.rows[0] ? this._deserializeVmSnapshot(result.rows[0]) : null;
  }
  
  /**
   * Deserialize job row from database
   * 
   * @private
   */
  _deserializeJob(row) {
    return {
      id: row.id,
      type: row.type,
      status: row.status,
      targetHostId: row.target_host_id,
      targetVmId: row.target_vm_id,
      payload: row.payload,
      result: row.result,
      error: row.error,
      claimedBy: row.claimed_by,
      claimedAt: row.claimed_at,
      startedAt: row.started_at,
      finishedAt: row.finished_at,
      attempts: row.attempts,
      maxAttempts: row.max_attempts,
      createdAt: row.created_at,
      updatedAt: row.updated_at
    };
  }
  
  /**
   * Deserialize job event row from database
   * 
   * @private
   */
  _deserializeJobEvent(row) {
    return {
      id: row.id,
      jobId: row.job_id,
      level: row.level,
      message: row.message,
      metadata: row.metadata,
      createdAt: row.created_at
    };
  }

  _deserializeUser(row) {
    return {
      id: row.id,
      username: row.username,
      role: row.role,
      created_at: row.created_at,
      updated_at: row.updated_at,
    };
  }

  _deserializeNetworkGroup(row) {
    return {
      id: row.id,
      owner_user_id: row.owner_user_id,
      name: row.name,
      libvirt_network_name: row.libvirt_network_name,
      bridge_name: row.bridge_name,
      subnet_cidr: row.subnet_cidr,
      gateway_ip: row.gateway_ip,
      dhcp_start: row.dhcp_start,
      dhcp_end: row.dhcp_end,
      profile: row.profile,
      created_at: row.created_at,
      updated_at: row.updated_at,
    };
  }

  _deserializeVmDefinition(row) {
    return {
      id: row.id,
      vm_name: row.vm_name,
      owner_user_id: row.owner_user_id,
      network_group_id: row.network_group_id,
      target_host_id: row.target_host_id,
      config: row.config,
      ssh_public_key: row.ssh_public_key,
      setup_script: row.setup_script,
      created_at: row.created_at,
      updated_at: row.updated_at,
    };
  }

  _deserializeVmRuntimeState(row) {
    return {
      vm_name: row.vm_name,
      state: row.state,
      observed_at: row.observed_at,
      observation_source: row.observation_source,
      created_at: row.created_at,
      updated_at: row.updated_at,
    };
  }

  _deserializeVmSnapshot(row) {
    return {
      vm_name: row.vm_name,
      snapshot_id: row.snapshot_id,
      metadata: row.metadata,
      created_at: row.created_at,
      updated_at: row.updated_at,
    };
  }

  /**
   * Store or update VM log snapshot
   * 
   * @param {string} vmName - VM name
   * @param {string} logContent - Log content
   * @param {number} lineCount - Number of lines
   * @param {string} collectedBy - Source identifier
   * @returns {Promise<object>} Stored log snapshot
   */
  async storeVmLogSnapshot(vmName, logContent, lineCount, collectedBy = 'worker') {
    // Enforce 1MB size limit per VM log snapshot
    const MAX_LOG_SIZE = 1024 * 1024; // 1MB in bytes
    const logSizeBytes = Buffer.byteLength(logContent, 'utf8');
    
    if (logSizeBytes > MAX_LOG_SIZE) {
      throw new Error(`Log content exceeds 1MB limit (${logSizeBytes} bytes). Truncate before storing.`);
    }
    
    const result = await this.pool.query(
      `INSERT INTO vm_log_snapshots (vm_name, log_content, line_count, collected_by, snapshot_at)
       VALUES ($1, $2, $3, $4, NOW())
       ON CONFLICT (vm_name) DO UPDATE
       SET log_content = EXCLUDED.log_content,
           line_count = EXCLUDED.line_count,
           collected_by = EXCLUDED.collected_by,
           snapshot_at = NOW()
       RETURNING *`,
      [vmName, logContent, lineCount, collectedBy]
    );

    return this._deserializeVmLogSnapshot(result.rows[0]);
  }

  /**
   * Get VM log snapshot
   * 
   * @param {string} vmName - VM name
   * @returns {Promise<object|null>} Log snapshot or null
   */
  async getVmLogSnapshot(vmName) {
    const result = await this.pool.query(
      'SELECT * FROM vm_log_snapshots WHERE vm_name = $1',
      [vmName]
    );

    return result.rows.length > 0 ? this._deserializeVmLogSnapshot(result.rows[0]) : null;
  }

  /**
   * List all VM log snapshots
   * 
   * @returns {Promise<Array>} All log snapshots
   */
  async listVmLogSnapshots() {
    const result = await this.pool.query(
      'SELECT * FROM vm_log_snapshots ORDER BY snapshot_at DESC'
    );

    return result.rows.map((row) => this._deserializeVmLogSnapshot(row));
  }

  /**
   * Delete VM log snapshot
   * 
   * @param {string} vmName - VM name
   * @returns {Promise<boolean>} True if deleted
   */
  async deleteVmLogSnapshot(vmName) {
    const result = await this.pool.query(
      'DELETE FROM vm_log_snapshots WHERE vm_name = $1',
      [vmName]
    );

    return result.rowCount > 0;
  }

  _deserializeVmLogSnapshot(row) {
    if (!row) return null;

    return {
      id: row.id,
      vm_name: row.vm_name,
      snapshot_at: row.snapshot_at,
      log_content: row.log_content,
      line_count: row.line_count,
      collected_by: row.collected_by,
    };
  }
}

/**
 * Create a new repository instance with connection pool
 * 
 * @param {string} databaseUrl - PostgreSQL connection URL
 * @param {object} poolConfig - Optional pool configuration
 * @returns {Promise<JobRepository>} Repository instance
 */
export async function createRepository(databaseUrl, poolConfig = {}) {
  if (!databaseUrl) {
    throw new Error('DATABASE_URL is required');
  }
  
  const pool = new pg.Pool({
    connectionString: databaseUrl,
    max: poolConfig.max || 20,
    idleTimeoutMillis: poolConfig.idleTimeoutMillis || 30000,
    connectionTimeoutMillis: poolConfig.connectionTimeoutMillis || 2000,
  });
  
  // Test connection
  try {
    const client = await pool.connect();
    client.release();
  } catch (error) {
    throw new Error(`Failed to connect to database: ${error.message}`);
  }
  
  return new JobRepository(pool);
}

/**
 * Close repository and release all connections
 * 
 * @param {JobRepository} repository - Repository instance
 * @returns {Promise<void>}
 */
export async function closeRepository(repository) {
  await repository.pool.end();
}
