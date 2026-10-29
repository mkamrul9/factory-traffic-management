import { describe, expect, it } from 'vitest';
import { nextSequence } from './models';
import { parseJunctions, parseStatus } from './response-validation';
import { statusFixture } from './status.fixture';

describe('Backend response validation', () => {
  it('preserves UNKNOWN observations instead of inventing red signals', () => {
    const body = statusFixture({
      actual_signals: { NORTH: 'UNKNOWN', SOUTH: 'UNKNOWN', EAST: 'RED', WEST: 'RED' },
    });
    expect(parseStatus(body).actual_signals.NORTH).toBe('UNKNOWN');
  });

  it.each([
    { actual_signals: { NORTH: 'GREEN' } },
    { desired_signals: { NORTH: 'BLUE', SOUTH: 'RED', EAST: 'RED', WEST: 'RED' } },
    { queues: { NORTH: -1, SOUTH: 0, EAST: 0, WEST: 0 } },
    { controller_status: 'BROKEN' },
    { awaiting_confirmation: 'yes' },
    { alerts: null },
    { pending_commands: [{ command_id: 'cmd-1' }] },
    { queued_vehicles: [{ vehicle_id: 'VH-1', direction: 'UP' }] },
    { next_transition_at: 'not-a-date' },
    { server_time: undefined },
  ])('rejects incomplete or invalid state before it reaches the controls: %j', (changes) => {
    expect(() => parseStatus({ ...statusFixture(), ...changes })).toThrow('incomplete');
  });

  it('does not treat a malformed junction list as an empty network', () => {
    expect(() => parseJunctions({ items: [] })).toThrow('incomplete');
    expect(() => parseJunctions([{ junction_id: 'A' }])).toThrow('incomplete');
  });

  it('uses a greater sequence than the last server cursor and guards unsafe integers', () => {
    const previous = Date.now() + 100;
    expect(nextSequence(previous)).toBe(previous + 1);
    expect(() => nextSequence(Number.MAX_SAFE_INTEGER)).toThrow('safely');
    expect(() => nextSequence(Number.MAX_SAFE_INTEGER + 1)).toThrow('safely');
  });
});
