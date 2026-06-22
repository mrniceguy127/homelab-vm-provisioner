import test from 'node:test';
import assert from 'node:assert/strict';

import { JobRepository } from '../src/repository.js';

function createPool(queryResults = []) {
  const queue = [...queryResults];
  return {
    queries: [],
    async query(sql, params = []) {
      this.queries.push({ sql, params });
      return queue.shift() || { rows: [] };
    },
    async connect() {
      const pool = this;
      return {
        async query(sql, params = []) {
          pool.queries.push({ sql, params });
          return queue.shift() || { rows: [] };
        },
        release() {},
      };
    },
  };
}

test('upsertVmDefinition stores canonical VM definition', async () => {
  const pool = createPool([{ rows: [{ id: 7, vm_name: 'demo', owner_user_id: 'user-admin', network_group_id: 'ng-demo', target_host_id: 'host1', config: { vm: { name: 'demo' } }, ssh_public_key: 'ssh', setup_script: 'echo hi', created_at: new Date(), updated_at: new Date() }] }]);
  const repository = new JobRepository(pool);

  const vmDefinition = await repository.upsertVmDefinition({
    vm_name: 'demo',
    owner_user_id: 'user-admin',
    network_group_id: 'ng-demo',
    target_host_id: 'host1',
    config: { vm: { name: 'demo' } },
    ssh_public_key: 'ssh',
    setup_script: 'echo hi',
  });

  assert.equal(vmDefinition.id, 7);
  assert.equal(pool.queries.length, 1);
  assert.match(pool.queries[0].sql, /INSERT INTO vm_definitions/);
});

test('upsertVmDefinitionAndEnqueueJob stores definition and job in one transaction', async () => {
  const now = new Date();
  const pool = createPool([
    { rows: [] },
    { rows: [{ id: 7, vm_name: 'demo', owner_user_id: 'user-admin', network_group_id: 'ng-demo', target_host_id: 'host1', config: { vm: { name: 'demo' } }, ssh_public_key: null, setup_script: null, created_at: now, updated_at: now }] },
    { rows: [{ id: 11, type: 'provision_vm', status: 'queued', target_host_id: 'host1', target_vm_id: 'demo', payload: { vmName: 'demo' }, result: null, error: null, claimed_by: null, claimed_at: null, started_at: null, finished_at: null, attempts: 0, max_attempts: 3, created_at: now, updated_at: now }] },
    { rows: [] },
  ]);
  const repository = new JobRepository(pool);

  const result = await repository.upsertVmDefinitionAndEnqueueJob(
    {
      vm_name: 'demo',
      owner_user_id: 'user-admin',
      network_group_id: 'ng-demo',
      target_host_id: 'host1',
      config: { vm: { name: 'demo' } },
      ssh_public_key: null,
      setup_script: null,
    },
    'provision_vm',
    { vmName: 'demo' },
    { targetVmId: 'demo', maxAttempts: 3, targetHostId: 'host1' },
  );

  assert.equal(result.vmDefinition.vm_name, 'demo');
  assert.equal(result.job.id, 11);
  assert.equal(pool.queries[0].sql, 'BEGIN');
  assert.equal(pool.queries.at(-1).sql, 'COMMIT');
});

test('upsertVmSnapshot stores snapshot metadata', async () => {
  const now = new Date();
  const pool = createPool([{ rows: [{ vm_name: 'demo', snapshot_id: 'snap-1', metadata: { artifact_manifest: { disk: '/tmp/disk' } }, created_at: now, updated_at: now }] }]);
  const repository = new JobRepository(pool);

  const snapshot = await repository.upsertVmSnapshot('demo', 'snap-1', { artifact_manifest: { disk: '/tmp/disk' } });

  assert.equal(snapshot.vm_name, 'demo');
  assert.equal(snapshot.snapshot_id, 'snap-1');
  assert.match(pool.queries[0].sql, /INSERT INTO vm_snapshots/);
});

test('storeVmLogSnapshot stores log content under 1MB', async () => {
  const now = new Date();
  const logContent = 'line1\nline2\nline3\n';
  const pool = createPool([{ rows: [{ id: 1, vm_name: 'test-vm', log_content: logContent, line_count: 3, collected_by: 'worker', snapshot_at: now }] }]);
  const repository = new JobRepository(pool);

  const logSnapshot = await repository.storeVmLogSnapshot('test-vm', logContent, 3, 'worker');
  
  assert.equal(logSnapshot.vm_name, 'test-vm');
  assert.equal(logSnapshot.line_count, 3);
  assert.equal(logSnapshot.collected_by, 'worker');
  assert.equal(pool.queries.length, 1);
  assert.match(pool.queries[0].sql, /INSERT INTO vm_log_snapshots/);
});

test('storeVmLogSnapshot rejects logs exceeding 1MB', async () => {
  const pool = createPool([]);
  const repository = new JobRepository(pool);
  
  // Create a log content larger than 1MB
  const largeLine = 'x'.repeat(1024); // 1KB line
  const largeLog = (largeLine + '\n').repeat(1025); // >1MB
  
  await assert.rejects(
    async () => {
      await repository.storeVmLogSnapshot('test-vm', largeLog, 1025, 'worker');
    },
    {
      message: /Log content exceeds 1MB limit/
    }
  );
  
  // Should not have made any database queries
  assert.equal(pool.queries.length, 0);
});
