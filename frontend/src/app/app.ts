import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject, signal, viewChild } from '@angular/core';
import { Router, RouterLink, RouterOutlet } from '@angular/router';
import { DashboardStore } from './core/dashboard-store';
import { Icon } from './shared/icon';
import { Topbar } from './shared/topbar';
import { CreateJunctionDialog } from './shared/create-junction-dialog';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, DatePipe, Icon, Topbar, CreateJunctionDialog],
  templateUrl: './app.html',
  styleUrl: './app.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class App {
  readonly store = inject(DashboardStore);
  readonly router = inject(Router);
  readonly menuOpen = signal(false);
  readonly dialog = viewChild.required<CreateJunctionDialog>('junctionDialog');
  openCreate(): void {
    this.dialog().open();
  }
}
