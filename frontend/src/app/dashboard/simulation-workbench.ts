import { DatePipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  inject,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DashboardStore } from '../core/dashboard-store';
import {
  DeviceStatus,
  DeviceType,
  Direction,
  DIRECTIONS,
  SignalColor,
  titleCase,
  VEHICLE_TYPES,
  vehicleLabel,
  VehicleType,
} from '../core/models';
import { Icon } from '../shared/icon';

@Component({
  selector: 'app-simulation-workbench',
  imports: [DatePipe, FormsModule, Icon],
  templateUrl: './simulation-workbench.html',
  styleUrl: './simulation-workbench.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SimulationWorkbench {
  readonly store = inject(DashboardStore);
  readonly directions = DIRECTIONS;
  readonly vehicleTypes = VEHICLE_TYPES;
  readonly titleCase = titleCase;
  readonly vehicleLabel = vehicleLabel;
  readonly simulationTab = signal<'traffic' | 'devices' | 'controller'>('traffic');
  readonly ackCommandId = signal('');
  readonly ackActual = signal<SignalColor>('RED');
  vehicleDirection: Direction = 'NORTH';
  vehicleType: VehicleType = 'TRUCK';
  vehicleId = '';
  clearVehicleId = '';
  deviceType: DeviceType = 'SIGNAL_CONTROLLER';
  deviceDirection: Direction = 'NORTH';
  deviceStatus: DeviceStatus = 'ONLINE';
  ackStatus: 'ACK' | 'FAILED' = 'ACK';
  readonly selectedCommand = computed(() =>
    this.store
      .status()
      ?.pending_commands.find((command) => command.command_id === this.ackCommandId()),
  );

  constructor() {
    effect(() => {
      this.store.selectedId();
      this.clearVehicleId = '';
      this.vehicleId = '';
    });
    effect(() => {
      const pending = this.store.status()?.pending_commands ?? [];
      if (!pending.some((command) => command.command_id === this.ackCommandId())) {
        this.ackCommandId.set(pending[0]?.command_id ?? '');
        this.ackActual.set(pending[0]?.requested_state ?? 'RED');
      }
    });
  }
  get vehicleIdValid(): boolean {
    return (
      this.vehicleId.trim() === '' ||
      /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(this.vehicleId.trim())
    );
  }
  get clearanceAvailable(): boolean {
    return !!this.store
      .status()
      ?.queued_vehicles.some((vehicle) => vehicle.vehicle_id === this.clearVehicleId);
  }
  async addVehicle(): Promise<void> {
    if (await this.store.vehicle(this.vehicleDirection, this.vehicleType, this.vehicleId))
      this.vehicleId = '';
  }
  async clearVehicle(): Promise<void> {
    const vehicle = this.store
      .status()
      ?.queued_vehicles.find((item) => item.vehicle_id === this.clearVehicleId);
    if (vehicle && (await this.store.clear(vehicle))) this.clearVehicleId = '';
  }
  selectCommand(id: string): void {
    this.ackCommandId.set(id);
    this.ackActual.set(
      this.store.status()?.pending_commands.find((command) => command.command_id === id)
        ?.requested_state ?? 'RED',
    );
  }
}
