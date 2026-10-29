import { ChangeDetectionStrategy, Component, inject, output } from '@angular/core';
import { DashboardStore } from '../core/dashboard-store';
import { Icon } from './icon';

@Component({
  selector: 'app-topbar',
  imports: [Icon],
  templateUrl: './topbar.html',
  styleUrl: './topbar.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class Topbar {
  readonly store = inject(DashboardStore);
  readonly addJunction = output<void>();
  readonly toggleMenu = output<void>();
}
