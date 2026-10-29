import { HttpErrorResponse, provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DashboardStore, errorMessage } from './dashboard-store';
import { JunctionStatus } from './models';
import { statusFixture } from './status.fixture';

describe('Dashboard polling and operator intents', () => {
  let store: DashboardStore;
  let http: HttpTestingController;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-10-29T08:00:00Z'));
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    store = TestBed.inject(DashboardStore);
    http = TestBed.inject(HttpTestingController);
    http.expectOne('/api/junctions').flush([{ junction_id: 'A', name: 'Junction A' }]);
    store.selectedId.set('A');
  });

  afterEach(() => {
    http.verify({ ignoreCancelled: true });
    TestBed.resetTestingModule();
    vi.useRealTimers();
  });

  function flushSnapshot(body: JunctionStatus = statusFixture(), id = 'A'): void {
    http
      .expectOne(`/api/junctions/${id}/history?limit=30`)
      .flush({ items: [], next_before_id: null });
    http.expectOne(`/api/junctions/${id}/status`).flush(body);
  }

  async function ready(): Promise<void> {
    const refresh = store.refresh();
    flushSnapshot();
    await refresh;
  }

  it('pauses writes when a received observation becomes stale', async () => {
    await ready();
    expect(store.canWrite()).toBe(true);
    await vi.advanceTimersByTimeAsync(4000);
    expect(store.fresh()).toBe(false);
    expect(await store.command('MANUAL_GREEN_REQUEST', 'NORTH')).toBe(false);
    http.expectNone('/api/junctions/A/commands');
    expect(store.status()?.actual_signals.NORTH).toBe('RED');
  });

  it('keeps the last snapshot and disables actions after a failed poll', async () => {
    await ready();
    const refresh = store.refresh();
    http.expectOne('/api/junctions/A/history?limit=30');
    http.expectOne('/api/junctions/A/status').error(new ProgressEvent('error'));
    await refresh;
    expect(store.canWrite()).toBe(false);
    expect(store.status()?.junction_id).toBe('A');
    expect(store.loadError()).toContain('Cannot reach');
  });

  it('does not let a late poll overwrite a newer snapshot', async () => {
    const first = store.refresh();
    const oldHistory = http.expectOne('/api/junctions/A/history?limit=30');
    const oldStatus = http.expectOne('/api/junctions/A/status');
    const second = store.refresh();
    const newer = statusFixture({
      actual_signals: { NORTH: 'GREEN', SOUTH: 'GREEN', EAST: 'RED', WEST: 'RED' },
      stage: 'GREEN',
      phase: 'NORTH_SOUTH',
    });
    flushSnapshot(newer);
    await second;
    oldHistory.flush({ items: [], next_before_id: null });
    oldStatus.flush(statusFixture());
    await first;
    expect(store.status()?.actual_signals.NORTH).toBe('GREEN');
  });

  it('discards an earlier junction response after route selection changes', async () => {
    const first = store.refresh();
    const oldHistory = http.expectOne('/api/junctions/A/history?limit=30');
    const oldStatus = http.expectOne('/api/junctions/A/status');
    store.selectJunction('B');
    expect(store.status()).toBeNull();
    await vi.advanceTimersByTimeAsync(0);
    flushSnapshot(statusFixture({ junction_id: 'B' }), 'B');
    await Promise.resolve();
    oldHistory.flush({ items: [], next_before_id: null });
    oldStatus.flush(statusFixture());
    await first;
    expect(store.status()?.junction_id).toBe('B');
  });

  it('sends a manual intent without changing lights optimistically', async () => {
    await ready();
    const action = store.command('MANUAL_GREEN_REQUEST', 'EAST');
    const request = http.expectOne('/api/junctions/A/commands');
    expect(request.request.body).toEqual({ command: 'MANUAL_GREEN_REQUEST', direction: 'EAST' });
    expect(store.busy()).toBe(true);
    expect(store.status()?.actual_signals.EAST).toBe('RED');
    request.flush({
      outcome: 'ACCEPTED',
      code: 'MANUAL_GREEN_REQUEST',
      message: 'Operator intent accepted.',
      duplicate: false,
    });
    await Promise.resolve();
    expect(store.status()?.actual_signals.EAST).toBe('RED');
    flushSnapshot(statusFixture({ mode: 'MANUAL' }));
    expect(await action).toBe(true);
    expect(store.busy()).toBe(false);
  });

  it('shows backend rejection messages and refreshes instead of crashing', async () => {
    await ready();
    const action = store.command('MANUAL_GREEN_REQUEST', 'EAST');
    http
      .expectOne('/api/junctions/A/commands')
      .flush(
        { message: 'Manual control cannot override an emergency.' },
        { status: 409, statusText: 'Conflict' },
      );
    await Promise.resolve();
    flushSnapshot(statusFixture({ mode: 'EMERGENCY' }));
    expect(await action).toBe(false);
    expect(store.notice()?.message).toContain('emergency');
    expect(store.canManual()).toBe(false);
  });

  it('resends the exact logical event for duplicate-event demonstration', async () => {
    await ready();
    const arrival = store.vehicle('WEST', 'TRUCK', 'VH-42');
    const first = http.expectOne('/api/sensor-events');
    const payload = first.request.body;
    first.flush({
      outcome: 'ACCEPTED',
      code: 'VEHICLE_ARRIVED',
      message: 'Vehicle added.',
      duplicate: false,
    });
    await Promise.resolve();
    flushSnapshot();
    await arrival;
    const resend = store.resend();
    const duplicate = http.expectOne('/api/sensor-events');
    expect(duplicate.request.body).toEqual(payload);
    duplicate.flush({
      outcome: 'ACCEPTED',
      code: 'VEHICLE_ARRIVED',
      message: 'Vehicle added.',
      duplicate: true,
    });
    await Promise.resolve();
    flushSnapshot();
    await resend;
    expect(store.notice()?.message).toContain('already processed');
  });

  it('reports invalid backend data and leaves controls paused', async () => {
    const refresh = store.refresh();
    http.expectOne('/api/junctions/A/history?limit=30').flush({ items: [], next_before_id: null });
    http
      .expectOne('/api/junctions/A/status')
      .flush({ junction_id: 'A', actual_signals: { NORTH: 'GREEN' } });
    await refresh;
    expect(store.status()).toBeNull();
    expect(store.loadError()).toContain('incomplete');
    expect(store.canWrite()).toBe(false);
  });

  it('requires the backend controller state before enabling a manual request', async () => {
    const refresh = store.refresh();
    flushSnapshot(statusFixture({ mode: 'DEGRADED', control_active: false }));
    await refresh;
    expect(store.canManual()).toBe(false);
    expect(store.canWrite()).toBe(true); // Device reports and recovery remain available.
  });

  it('rejects malformed history without replacing the last valid snapshot', async () => {
    await ready();
    const refresh = store.refresh();
    http.expectOne('/api/junctions/A/status').flush(statusFixture());
    http.expectOne('/api/junctions/A/history?limit=30').flush({ items: [null] });
    await refresh;
    expect(store.status()?.junction_id).toBe('A');
    expect(store.loadError()).toBe('Activity data is incomplete. Please refresh.');
    expect(store.canWrite()).toBe(false);
  });
});

describe('User-facing API errors', () => {
  it('reads both operation and validation error envelopes', () => {
    expect(
      errorMessage(
        new HttpErrorResponse({ status: 409, error: { message: 'Device unavailable.' } }),
      ),
    ).toBe('Device unavailable.');
    expect(
      errorMessage(
        new HttpErrorResponse({
          status: 422,
          error: { error: { message: 'Request validation failed.' } },
        }),
      ),
    ).toBe('Request validation failed.');
    expect(errorMessage(new Error('Incomplete observation.'))).toBe('Incomplete observation.');
  });
});
