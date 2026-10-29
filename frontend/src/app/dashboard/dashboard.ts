import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { DashboardStore } from '../core/dashboard-store';
import { titleCase } from '../core/models';
import { Icon } from '../shared/icon';
import { ActivityFeed } from './activity-feed';
import { ControlPanel } from './control-panel';
import { Intersection } from './intersection';
import { JunctionMetrics } from './junction-metrics';
import { SimulationWorkbench } from './simulation-workbench';

@Component({
  selector: 'app-dashboard',
  imports: [
    RouterLink,
    Icon,
    Intersection,
    JunctionMetrics,
    SimulationWorkbench,
    ControlPanel,
    ActivityFeed,
  ],
  templateUrl: './dashboard.html',
  styleUrl: './dashboard.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class Dashboard {
  readonly store = inject(DashboardStore);
  readonly titleCase = titleCase;

  constructor() {
    inject(ActivatedRoute)
      .paramMap.pipe(takeUntilDestroyed())
      .subscribe((params) => {
        this.store.selectJunction(params.get('junctionId') ?? 'A');
      });
  }
}
