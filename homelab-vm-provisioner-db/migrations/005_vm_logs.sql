-- Migration: Add VM log storage
-- Description: Store VM logs collected by worker for API access

CREATE TABLE vm_log_snapshots (
  id SERIAL PRIMARY KEY,
  vm_name VARCHAR(255) NOT NULL,
  snapshot_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  log_content TEXT NOT NULL,
  line_count INTEGER NOT NULL,
  collected_by VARCHAR(50) NOT NULL DEFAULT 'worker',
  CONSTRAINT vm_log_snapshots_vm_name_key UNIQUE (vm_name)
);

CREATE INDEX idx_vm_log_snapshots_vm_name ON vm_log_snapshots(vm_name);
CREATE INDEX idx_vm_log_snapshots_snapshot_at ON vm_log_snapshots(snapshot_at);

COMMENT ON TABLE vm_log_snapshots IS 'VM log snapshots collected periodically by worker';
COMMENT ON COLUMN vm_log_snapshots.vm_name IS 'VM identifier';
COMMENT ON COLUMN vm_log_snapshots.snapshot_at IS 'When this snapshot was collected';
COMMENT ON COLUMN vm_log_snapshots.log_content IS 'Log content (last N lines)';
COMMENT ON COLUMN vm_log_snapshots.line_count IS 'Number of lines in this snapshot';
COMMENT ON COLUMN vm_log_snapshots.collected_by IS 'Source of collection (worker, manual, etc)';
