import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { DashboardStore } from '../core/dashboard-store';
import { titleCase } from '../core/models';
import { Icon } from '../shared/icon';

@Component({
  selector: 'app-junction-metrics',
  imports: [Icon],
  templateUrl: './junction-metrics.html',
  styleUrl: './junction-metrics.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class JunctionMetrics {
  readonly store = inject(DashboardStore);
  readonly titleCase = titleCase;
}
