import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { Direction, DIRECTIONS, JunctionStatus, PhysicalSignal, titleCase } from '../core/models';
import { Icon } from '../shared/icon';

@Component({
  selector: 'app-intersection',
  imports: [Icon],
  templateUrl: './intersection.html',
  styleUrl: './intersection.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class Intersection {
  readonly status = input.required<JunctionStatus>();
  readonly stale = input(false);
  readonly directions = DIRECTIONS;
  readonly titleCase = titleCase;
  readonly confirmationCaption = computed(() => {
    const state = this.status();
    if (state.awaiting_confirmation) return 'AWAITING ACK';
    if (DIRECTIONS.some((direction) => state.actual_signals[direction] === 'UNKNOWN'))
      return 'UNKNOWN';
    if (
      DIRECTIONS.some(
        (direction) => state.actual_signals[direction] !== state.desired_signals[direction],
      )
    )
      return 'MISMATCH';
    return 'CONFIRMED';
  });
  actual(direction: Direction): PhysicalSignal {
    return this.status().actual_signals[direction];
  }
  lamp(direction: Direction, color: PhysicalSignal): string {
    return this.actual(direction) === color ? color.toLowerCase() : 'off';
  }
}
