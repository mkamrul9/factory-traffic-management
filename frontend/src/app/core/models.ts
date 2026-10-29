export const DIRECTIONS = ['NORTH', 'SOUTH', 'EAST', 'WEST'] as const;
export type Direction = (typeof DIRECTIONS)[number];
export const VEHICLE_TYPES = ['TRUCK', 'FORKLIFT', 'EMPLOYEE_VEHICLE', 'EMERGENCY'] as const;
export type VehicleType = (typeof VEHICLE_TYPES)[number];
export type SignalColor = 'RED' | 'YELLOW' | 'GREEN';
export type PhysicalSignal = SignalColor | 'UNKNOWN';
export type DeviceStatus = 'ONLINE' | 'OFFLINE' | 'UNKNOWN';
export type DeviceType = 'SIGNAL_CONTROLLER' | 'SIGNAL' | 'SENSOR';
export type OperatingMode = 'AUTOMATIC' | 'MANUAL' | 'EMERGENCY' | 'DEGRADED';
export type OperatorCommand = 'MANUAL_GREEN_REQUEST' | 'RETURN_TO_AUTOMATIC' | 'RECOVER_CONTROLLER';

export interface Junction {
  junction_id: string;
  name: string;
}

export interface WaitingVehicle {
  vehicle_id: string;
  direction: Direction;
  vehicle_type: VehicleType;
  arrived_at: string;
}

export interface ControllerCommand {
  command_id: string;
  junction_id: string;
  batch_id: string;
  direction: Direction;
  requested_state: SignalColor;
  deadline: string;
}

export interface JunctionStatus {
  junction_id: string;
  mode: OperatingMode;
  phase: string;
  stage: 'ALL_RED' | 'GREEN' | 'YELLOW';
  target_phase_id: string | null;
  control_active: boolean;
  controller_status: DeviceStatus;
  desired_signals: Record<Direction, SignalColor>;
  actual_signals: Record<Direction, PhysicalSignal>;
  queues: Record<Direction, number>;
  queued_vehicles: WaitingVehicle[];
  emergency_directions: Direction[];
  alerts: { code: string; message: string }[];
  awaiting_confirmation: boolean;
  next_transition_at: string | null;
  manual_expires_at: string | null;
  batch_id: string | null;
  pending_commands: ControllerCommand[];
  simulator_auto_ack: boolean;
  sensor_statuses: Record<Direction, DeviceStatus>;
  signal_statuses: Record<Direction, DeviceStatus>;
  sequence_numbers: Record<Direction, number | null>;
  server_time: string;
}

export interface AuditEntry {
  id: number;
  event_type: string;
  event_id: string | null;
  timestamp: string;
  details: Record<string, unknown>;
}

export interface HistoryPage {
  items: AuditEntry[];
  next_before_id: number | null;
}

export interface OperationResult {
  outcome: 'ACCEPTED' | 'IGNORED' | 'REJECTED';
  code: string;
  message: string;
  duplicate: boolean;
}

export interface SensorEvent {
  event_id: string;
  junction_id: string;
  direction: Direction;
  event_type: 'VEHICLE_ARRIVED' | 'VEHICLE_CLEARED';
  vehicle_id: string;
  vehicle_type?: VehicleType;
  sequence_no: number;
  timestamp: string;
}

export function titleCase(value: string): string {
  return value
    .toLowerCase()
    .replaceAll('_', ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function vehicleLabel(value: VehicleType): string {
  return value === 'EMPLOYEE_VEHICLE' ? 'Employee vehicle' : titleCase(value);
}

export function nextSequence(previous: number | null): number {
  if (
    previous !== null &&
    (!Number.isSafeInteger(previous) || previous >= Number.MAX_SAFE_INTEGER)
  ) {
    throw new Error(
      'This sequence cannot be safely simulated in the browser. Use the API for this stream.',
    );
  }
  return Math.max((previous ?? -1) + 1, Date.now());
}
