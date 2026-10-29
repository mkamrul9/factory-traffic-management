import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DashboardStore } from '../core/dashboard-store';
import { Direction, DIRECTIONS, titleCase } from '../core/models';
import { Icon } from '../shared/icon';

@Component({
  selector: 'app-control-panel',
  imports: [DatePipe, FormsModule, Icon],
  templateUrl: './control-panel.html',
  styleUrl: './control-panel.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ControlPanel {
  readonly store = inject(DashboardStore);
  readonly directions = DIRECTIONS;
  readonly titleCase = titleCase;
  manualDirection: Direction = 'NORTH';
}
