import { DIRECTIONS, Junction, JunctionStatus } from './models';

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

const colors = ['RED', 'YELLOW', 'GREEN'];
const devices = ['ONLINE', 'OFFLINE', 'UNKNOWN'];
const modes = ['AUTOMATIC', 'MANUAL', 'EMERGENCY', 'DEGRADED'];
const vehicles = ['TRUCK', 'FORKLIFT', 'EMPLOYEE_VEHICLE', 'EMERGENCY'];
const member = (values: readonly string[], value: unknown) =>
  typeof value === 'string' && values.includes(value);
const date = (value: unknown) => typeof value === 'string' && Number.isFinite(Date.parse(value));

export function parseJunctions(value: unknown): Junction[] {
  if (
    !Array.isArray(value) ||
    !value.every(
      (item) =>
        record(item) && typeof item['junction_id'] === 'string' && typeof item['name'] === 'string',
    )
  ) {
    throw new Error('The junction list is incomplete. Please refresh.');
  }
  return value as Junction[];
}

export function parseStatus(value: unknown): JunctionStatus {
  const invalid = () =>
    new Error('Junction data is incomplete. Controls are paused until a valid update arrives.');
  if (
    !record(value) ||
    typeof value['junction_id'] !== 'string' ||
    !member(modes, value['mode']) ||
    !member(['ALL_RED', 'GREEN', 'YELLOW'], value['stage']) ||
    typeof value['phase'] !== 'string'
  )
    throw invalid();
  for (const field of [
    'desired_signals',
    'actual_signals',
    'queues',
    'sensor_statuses',
    'signal_statuses',
    'sequence_numbers',
  ]) {
    if (!record(value[field])) throw invalid();
  }
  const desired = value['desired_signals'] as Record<string, unknown>;
  const actual = value['actual_signals'] as Record<string, unknown>;
  const queues = value['queues'] as Record<string, unknown>;
  const sensors = value['sensor_statuses'] as Record<string, unknown>;
  const signals = value['signal_statuses'] as Record<string, unknown>;
  const sequences = value['sequence_numbers'] as Record<string, unknown>;
  for (const direction of DIRECTIONS) {
    if (
      !member(colors, desired[direction]) ||
      !member([...colors, 'UNKNOWN'], actual[direction]) ||
      !member(devices, sensors[direction]) ||
      !member(devices, signals[direction])
    )
      throw invalid();
    const count = queues[direction];
    const sequence = sequences[direction];
    if (typeof count !== 'number' || !Number.isInteger(count) || count < 0) throw invalid();
    if (
      sequence !== null &&
      (typeof sequence !== 'number' || !Number.isInteger(sequence) || sequence < 0)
    )
      throw invalid();
  }
  if (!member(devices, value['controller_status']) || !date(value['server_time'])) throw invalid();
  for (const key of ['control_active', 'awaiting_confirmation', 'simulator_auto_ack'])
    if (typeof value[key] !== 'boolean') throw invalid();
  for (const key of ['next_transition_at', 'manual_expires_at'])
    if (value[key] !== null && !date(value[key])) throw invalid();
  if (value['batch_id'] !== null && typeof value['batch_id'] !== 'string') throw invalid();
  if (value['target_phase_id'] !== null && typeof value['target_phase_id'] !== 'string')
    throw invalid();
  if (
    !Array.isArray(value['queued_vehicles']) ||
    !value['queued_vehicles'].every(
      (item) =>
        record(item) &&
        typeof item['vehicle_id'] === 'string' &&
        member(DIRECTIONS, item['direction']) &&
        member(vehicles, item['vehicle_type']) &&
        date(item['arrived_at']),
    )
  )
    throw invalid();
  if (
    !Array.isArray(value['pending_commands']) ||
    !value['pending_commands'].every(
      (item) =>
        record(item) &&
        typeof item['command_id'] === 'string' &&
        typeof item['batch_id'] === 'string' &&
        member(DIRECTIONS, item['direction']) &&
        member(colors, item['requested_state']) &&
        date(item['deadline']),
    )
  )
    throw invalid();
  if (
    !Array.isArray(value['emergency_directions']) ||
    !value['emergency_directions'].every((item) => member(DIRECTIONS, item))
  )
    throw invalid();
  if (
    !Array.isArray(value['alerts']) ||
    !value['alerts'].every(
      (item) =>
        record(item) && typeof item['code'] === 'string' && typeof item['message'] === 'string',
    )
  )
    throw invalid();
  return value as unknown as JunctionStatus;
}
