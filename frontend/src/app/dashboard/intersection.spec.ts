import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';
import { statusFixture } from '../core/status.fixture';
import { Intersection } from './intersection';

describe('Intersection observations', () => {
  it('renders actual UNKNOWN separately from requested GREEN', async () => {
    const fixture = TestBed.createComponent(Intersection);
    fixture.componentRef.setInput(
      'status',
      statusFixture({
        desired_signals: { NORTH: 'GREEN', SOUTH: 'GREEN', EAST: 'RED', WEST: 'RED' },
        actual_signals: { NORTH: 'UNKNOWN', SOUTH: 'UNKNOWN', EAST: 'RED', WEST: 'RED' },
        awaiting_confirmation: true,
      }),
    );
    fixture.detectChanges();
    await fixture.whenStable();
    const element = fixture.nativeElement as HTMLElement;
    expect(element.querySelectorAll('.unknown-mark')).toHaveLength(2);
    expect(element.querySelectorAll('circle.green')).toHaveLength(0);
    expect(element.querySelector('.approach .signal-label')?.textContent).toContain('Unknown');
    expect(element.querySelector('.approach-meta')?.textContent).toContain('Green');
    expect(element.querySelector('.center-caption')?.textContent).toContain('AWAITING ACK');
  });

  it('does not claim confirmation when a second fault invalidates previously confirmed signals', async () => {
    const fixture = TestBed.createComponent(Intersection);
    fixture.componentRef.setInput(
      'status',
      statusFixture({
        mode: 'DEGRADED',
        control_active: false,
        controller_status: 'OFFLINE',
        actual_signals: {
          NORTH: 'UNKNOWN',
          SOUTH: 'UNKNOWN',
          EAST: 'UNKNOWN',
          WEST: 'UNKNOWN',
        },
        awaiting_confirmation: false,
      }),
    );
    fixture.detectChanges();
    await fixture.whenStable();
    const element = fixture.nativeElement as HTMLElement;
    expect(element.querySelector('.center-caption')?.textContent).toContain('UNKNOWN');
    expect(element.querySelectorAll('circle.red')).toHaveLength(0);
    expect(element.querySelectorAll('.unknown-mark')).toHaveLength(4);
  });

  it('identifies a mismatch when known physical signals differ from the requested stage', async () => {
    const fixture = TestBed.createComponent(Intersection);
    fixture.componentRef.setInput(
      'status',
      statusFixture({
        stage: 'GREEN',
        phase: 'NORTH_SOUTH',
        desired_signals: { NORTH: 'GREEN', SOUTH: 'GREEN', EAST: 'RED', WEST: 'RED' },
      }),
    );
    fixture.detectChanges();
    await fixture.whenStable();
    const element = fixture.nativeElement as HTMLElement;
    expect(element.querySelector('.center-caption')?.textContent).toContain('MISMATCH');
    expect(element.querySelectorAll('circle.red')).toHaveLength(4);
  });

  it('marks a stale drawing without inventing a new signal state', async () => {
    const fixture = TestBed.createComponent(Intersection);
    fixture.componentRef.setInput('status', statusFixture());
    fixture.componentRef.setInput('stale', true);
    fixture.detectChanges();
    await fixture.whenStable();
    const element = fixture.nativeElement as HTMLElement;
    expect(element.querySelector('.stale-overlay')?.textContent).toContain(
      'Last confirmed snapshot',
    );
    expect(element.querySelectorAll('circle.red')).toHaveLength(4);
    expect(element.querySelector('.center-caption')?.textContent).toContain('CONFIRMED');
  });
});
