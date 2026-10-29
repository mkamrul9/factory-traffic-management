import { JunctionStatus } from './models';

export function statusFixture(changes: Partial<JunctionStatus> = {}): JunctionStatus {
  return {
    junction_id: 'A',
    mode: 'AUTOMATIC',
    phase: 'ALL_RED',
    stage: 'ALL_RED',
    target_phase_id: null,
    control_active: true,
    controller_status: 'ONLINE',
    desired_signals: { NORTH: 'RED', SOUTH: 'RED', EAST: 'RED', WEST: 'RED' },
    actual_signals: { NORTH: 'RED', SOUTH: 'RED', EAST: 'RED', WEST: 'RED' },
    queues: { NORTH: 0, SOUTH: 0, EAST: 0, WEST: 0 },
    queued_vehicles: [],
    emergency_directions: [],
    alerts: [],
    awaiting_confirmation: false,
    next_transition_at: '2026-10-29T08:00:02Z',
    manual_expires_at: null,
    batch_id: 'batch-1',
    pending_commands: [],
    simulator_auto_ack: true,
    sensor_statuses: { NORTH: 'ONLINE', SOUTH: 'ONLINE', EAST: 'ONLINE', WEST: 'ONLINE' },
    signal_statuses: { NORTH: 'ONLINE', SOUTH: 'ONLINE', EAST: 'ONLINE', WEST: 'ONLINE' },
    sequence_numbers: { NORTH: 1, SOUTH: null, EAST: null, WEST: null },
    server_time: '2026-10-29T08:00:00Z',
    ...changes,
  };
}
