import { HttpErrorResponse } from '@angular/common/http';
import { computed, DestroyRef, inject, Injectable, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import {
  exhaustMap,
  firstValueFrom,
  forkJoin,
  from,
  interval,
  Observable,
  Subject,
  switchMap,
  timer,
} from 'rxjs';
import { TrafficApi } from './traffic-api';
import {
  AuditEntry,
  DeviceStatus,
  DeviceType,
  Direction,
  Junction,
  JunctionStatus,
  nextSequence,
  OperationResult,
  OperatorCommand,
  SensorEvent,
  SignalColor,
  VehicleType,
  WaitingVehicle,
} from './models';

export interface Notice {
  tone: 'success' | 'warning' | 'error';
  message: string;
}

export function errorMessage(error: unknown): string {
  if (error instanceof Error && error.name === 'TimeoutError')
    return 'The traffic server is taking too long to respond. Please retry.';
  if (error instanceof HttpErrorResponse) {
    if (error.status === 0)
      return 'Cannot reach the traffic server. Check that the backend is running, then retry.';
    if (error.status === 404)
      return (
        error.error?.error?.message ??
        error.error?.message ??
        'This junction or controller command no longer exists.'
      );
    return (
      error.error?.error?.message ??
      error.error?.message ??
      'The request could not be completed. Please retry.'
    );
  }
  return error instanceof Error
    ? error.message
    : 'The request could not be completed. Please retry.';
}

@Injectable({ providedIn: 'root' })
export class DashboardStore {
  private readonly api = inject(TrafficApi);
  private readonly destroyRef = inject(DestroyRef);
  private readonly selections = new Subject<string>();
  private issued = 0;
  private applied = 0;
  private readonly clock = signal(Date.now());
  readonly selectedId = signal('');
  readonly junctions = signal<Junction[]>([]);
  readonly status = signal<JunctionStatus | null>(null);
  readonly history = signal<AuditEntry[]>([]);
  readonly loading = signal(true);
  readonly loadError = signal<string | null>(null);
  readonly listError = signal<string | null>(null);
  readonly lastUpdated = signal<number | null>(null);
  readonly busy = signal(false);
  readonly notice = signal<Notice | null>(null);
  readonly lastSensorEvent = signal<SensorEvent | null>(null);
  readonly fresh = computed(() => {
    const updated = this.lastUpdated();
    return updated !== null && this.loadError() === null && this.clock() - updated < 4000;
  });
  readonly canWrite = computed(
    () => this.fresh() && !this.busy() && this.status()?.junction_id === this.selectedId(),
  );
  readonly selectedName = computed(
    () =>
      this.junctions().find((j) => j.junction_id === this.selectedId())?.name ??
      `Junction ${this.selectedId()}`,
  );
  readonly totalWaiting = computed(() =>
    Object.values(this.status()?.queues ?? {}).reduce((sum, count) => sum + count, 0),
  );
  readonly canManual = computed(
    () =>
      this.canWrite() &&
      this.status()?.control_active === true &&
      this.status()?.mode !== 'EMERGENCY',
  );
  readonly devicesAvailable = computed(() => {
    const status = this.status();
    return (
      status !== null &&
      status.controller_status !== 'OFFLINE' &&
      Object.values(status.sensor_statuses).every((v) => v === 'ONLINE') &&
      Object.values(status.signal_statuses).every((v) => v === 'ONLINE')
    );
  });
  readonly canAcknowledge = computed(
    () =>
      this.canWrite() &&
      !!this.status()?.pending_commands.length &&
      this.status()?.controller_status !== 'OFFLINE' &&
      Object.values(this.status()?.signal_statuses ?? {}).every((v) => v === 'ONLINE'),
  );

  constructor() {
    interval(1000)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(() => this.clock.set(Date.now()));
    this.selections
      .pipe(
        switchMap((id) => timer(0, 1000).pipe(exhaustMap(() => from(this.refresh(id))))),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe();
    void this.loadJunctions();
  }

  selectJunction(id: string): void {
    if (id === this.selectedId()) return;
    this.selectedId.set(id);
    this.status.set(null);
    this.history.set([]);
    this.lastUpdated.set(null);
    this.loadError.set(null);
    this.notice.set(null);
    this.lastSensorEvent.set(null);
    this.loading.set(true);
    this.applied = ++this.issued;
    this.selections.next(id);
  }

  async loadJunctions(): Promise<void> {
    try {
      this.junctions.set(await firstValueFrom(this.api.junctions()));
      this.listError.set(null);
    } catch (error) {
      this.listError.set(errorMessage(error));
    }
  }

  async refresh(id = this.selectedId()): Promise<void> {
    if (!id) return;
    const token = ++this.issued;
    try {
      const snapshot = await firstValueFrom(
        forkJoin({ status: this.api.status(id), history: this.api.history(id) }),
      );
      // A late poll from an earlier route or request must not replace a newer observation.
      if (id !== this.selectedId() || token < this.applied) return;
      this.applied = token;
      this.status.set(snapshot.status);
      this.history.set(snapshot.history.items);
      this.lastUpdated.set(Date.now());
      this.clock.set(Date.now());
      this.loadError.set(null);
    } catch (error) {
      if (id !== this.selectedId() || token < this.applied) return;
      this.applied = token;
      this.loadError.set(errorMessage(error));
    } finally {
      if (id === this.selectedId()) this.loading.set(false);
    }
  }

  async perform<T>(
    operation: () => Observable<T>,
    success: (result: T) => Notice,
    requiresFresh = true,
  ): Promise<boolean> {
    if (this.busy()) return false;
    if (requiresFresh && !this.canWrite()) {
      this.notice.set({
        tone: 'warning',
        message: 'Refresh the junction state before sending an action.',
      });
      return false;
    }
    const id = this.selectedId();
    this.busy.set(true);
    this.notice.set(null);
    try {
      const result = await firstValueFrom(operation());
      const notice = success(result);
      if (id === this.selectedId()) this.notice.set(notice);
      return notice.tone !== 'error';
    } catch (error) {
      if (id === this.selectedId())
        this.notice.set({ tone: 'error', message: errorMessage(error) });
      return false;
    } finally {
      await this.refresh(id);
      this.busy.set(false);
    }
  }

  private operationNotice(result: OperationResult): Notice {
    return {
      tone:
        result.outcome === 'REJECTED'
          ? 'error'
          : result.outcome === 'IGNORED'
            ? 'warning'
            : 'success',
      message: result.duplicate
        ? 'This event was already processed. The queue is unchanged.'
        : result.message,
    };
  }

  command(command: OperatorCommand, direction?: Direction): Promise<boolean> {
    return this.perform(
      () => this.api.command(this.selectedId(), command, direction),
      (r) => this.operationNotice(r),
    );
  }
  simulator(enabled: boolean): Promise<boolean> {
    return this.perform(
      () => this.api.simulator(this.selectedId(), enabled),
      (r) => this.operationNotice(r),
    );
  }
  acknowledgeBatch(): Promise<boolean> {
    const batch = this.status()?.batch_id;
    if (!batch) return Promise.resolve(false);
    return this.perform(
      () => this.api.acknowledgeBatch(this.selectedId(), batch),
      (r) => this.operationNotice(r),
    );
  }
  acknowledge(command: string, status: 'ACK' | 'FAILED', actual: SignalColor): Promise<boolean> {
    return this.perform(
      () => this.api.acknowledge(this.selectedId(), command, status, actual),
      (r) => this.operationNotice(r),
    );
  }
  device(type: DeviceType, status: DeviceStatus, direction: Direction): Promise<boolean> {
    return this.perform(
      () => this.api.device(this.selectedId(), type, status, direction),
      (r) => this.operationNotice(r),
    );
  }

  async vehicle(direction: Direction, kind: VehicleType, vehicleId: string): Promise<boolean> {
    const snapshot = this.status();
    if (!snapshot) return false;
    try {
      const event: SensorEvent = {
        event_id: `evt-${crypto.randomUUID()}`,
        junction_id: snapshot.junction_id,
        direction,
        event_type: 'VEHICLE_ARRIVED',
        vehicle_id: vehicleId.trim() || `VH-${crypto.randomUUID().slice(0, 8).toUpperCase()}`,
        vehicle_type: kind,
        sequence_no: nextSequence(snapshot.sequence_numbers[direction]),
        timestamp: new Date().toISOString(),
      };
      this.lastSensorEvent.set(event);
      return await this.perform(
        () => this.api.sensor(event),
        (r) => this.operationNotice(r),
      );
    } catch (error) {
      this.notice.set({ tone: 'error', message: errorMessage(error) });
      return false;
    }
  }

  clear(vehicle: WaitingVehicle): Promise<boolean> {
    const snapshot = this.status();
    if (!snapshot) return Promise.resolve(false);
    try {
      const event: SensorEvent = {
        event_id: `evt-${crypto.randomUUID()}`,
        junction_id: snapshot.junction_id,
        direction: vehicle.direction,
        event_type: 'VEHICLE_CLEARED',
        vehicle_id: vehicle.vehicle_id,
        vehicle_type: vehicle.vehicle_type,
        sequence_no: nextSequence(snapshot.sequence_numbers[vehicle.direction]),
        timestamp: new Date().toISOString(),
      };
      this.lastSensorEvent.set(event);
      return this.perform(
        () => this.api.sensor(event),
        (r) => this.operationNotice(r),
      );
    } catch (error) {
      this.notice.set({ tone: 'error', message: errorMessage(error) });
      return Promise.resolve(false);
    }
  }

  resend(): Promise<boolean> {
    const event = this.lastSensorEvent();
    return event
      ? this.perform(
          () => this.api.sensor(event),
          (r) => this.operationNotice(r),
        )
      : Promise.resolve(false);
  }

  async createJunction(id: string, name: string): Promise<boolean> {
    const success = await this.perform(
      () => this.api.createJunction(id.trim(), name.trim()),
      () => ({
        tone: 'success',
        message: 'Junction created. Enable its controller simulator to begin recovery.',
      }),
      false,
    );
    if (success) await this.loadJunctions();
    return success;
  }
}
