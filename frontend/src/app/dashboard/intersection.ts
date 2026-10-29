import { ChangeDetectionStrategy, Component, input } from '@angular/core';
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
  actual(direction: Direction): PhysicalSignal {
    return this.status().actual_signals[direction];
  }
  lamp(direction: Direction, color: PhysicalSignal): string {
    return this.actual(direction) === color ? color.toLowerCase() : 'off';
  }
}
