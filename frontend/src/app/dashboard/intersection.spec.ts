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
  });
});
