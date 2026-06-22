-- Add runtime state observation metadata

ALTER TABLE vm_runtime_state
ADD COLUMN observed_at TIMESTAMPTZ,
ADD COLUMN observation_source VARCHAR(50);

COMMENT ON COLUMN vm_runtime_state.observed_at IS 'Timestamp when the runtime state was last observed';
COMMENT ON COLUMN vm_runtime_state.observation_source IS 'Source of the observation (e.g., worker_mutation, explicit_refresh, initial_provision)';
