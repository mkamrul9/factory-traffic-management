import { HttpClient } from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { map, Observable, timeout } from 'rxjs';
import {
  DeviceStatus,
  DeviceType,
  Direction,
  HistoryPage,
  Junction,
  JunctionStatus,
  OperationResult,
  OperatorCommand,
  SensorEvent,
  SignalColor,
} from './models';
import { parseJunctions, parseStatus } from './response-validation';

@Injectable({ providedIn: 'root' })
export class TrafficApi {
  private readonly http = inject(HttpClient);
  private junctionPath(id: string): string {
    return `/api/junctions/${encodeURIComponent(id)}`;
  }
  private post<T>(path: string, body: unknown): Observable<T> {
    return this.http.post<T>(path, body).pipe(timeout(8000));
  }

  junctions(): Observable<Junction[]> {
    return this.http.get<unknown>('/api/junctions').pipe(timeout(8000), map(parseJunctions));
  }
  status(id: string): Observable<JunctionStatus> {
    return this.http
      .get<unknown>(`${this.junctionPath(id)}/status`)
      .pipe(timeout(8000), map(parseStatus));
  }
  history(id: string): Observable<HistoryPage> {
    return this.http.get<HistoryPage>(`${this.junctionPath(id)}/history?limit=30`).pipe(
      timeout(8000),
      map((page) => {
        if (
          !page ||
          !Array.isArray(page.items) ||
          !page.items.every(
            (item) =>
              item !== null &&
              typeof item === 'object' &&
              typeof item.id === 'number' &&
              typeof item.event_type === 'string' &&
              typeof item.timestamp === 'string' &&
              Number.isFinite(Date.parse(item.timestamp)) &&
              item.details !== null &&
              typeof item.details === 'object' &&
              !Array.isArray(item.details),
          )
        )
          throw new Error('Activity data is incomplete. Please refresh.');
        return page;
      }),
    );
  }
  createJunction(id: string, name: string): Observable<Junction> {
    return this.post('/api/junctions', { junction_id: id, name });
  }
  sensor(event: SensorEvent): Observable<OperationResult> {
    return this.post('/api/sensor-events', event);
  }
  command(
    id: string,
    command: OperatorCommand,
    direction?: Direction,
  ): Observable<OperationResult> {
    return this.post(
      `${this.junctionPath(id)}/commands`,
      direction ? { command, direction } : { command },
    );
  }
  simulator(id: string, enabled: boolean): Observable<OperationResult> {
    return this.post(`${this.junctionPath(id)}/simulator`, { auto_ack: enabled });
  }
  acknowledgeBatch(id: string, batch: string): Observable<OperationResult> {
    return this.post(`${this.junctionPath(id)}/simulator/acknowledge`, { batch_id: batch });
  }
  acknowledge(
    id: string,
    command: string,
    status: 'ACK' | 'FAILED',
    actual: SignalColor,
  ): Observable<OperationResult> {
    return this.post('/api/controller-events', {
      junction_id: id,
      command_id: command,
      status,
      actual_state: actual,
      timestamp: new Date().toISOString(),
    });
  }
  device(
    id: string,
    type: DeviceType,
    status: DeviceStatus,
    direction: Direction,
  ): Observable<OperationResult> {
    return this.post('/api/device-events', {
      event_id: `status-${crypto.randomUUID()}`,
      junction_id: id,
      device_type: type,
      status,
      ...(type === 'SIGNAL_CONTROLLER' ? {} : { direction }),
      timestamp: new Date().toISOString(),
    });
  }
}
