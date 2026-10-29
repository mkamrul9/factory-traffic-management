import { ChangeDetectionStrategy, Component, ElementRef, inject, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { DashboardStore } from '../core/dashboard-store';
import { Icon } from './icon';

@Component({
  selector: 'app-create-junction-dialog',
  imports: [FormsModule, Icon],
  templateUrl: './create-junction-dialog.html',
  styleUrl: './create-junction-dialog.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CreateJunctionDialog {
  readonly store = inject(DashboardStore);
  private readonly router = inject(Router);
  private readonly nativeDialog = viewChild.required<ElementRef<HTMLDialogElement>>('dialog');
  newId = '';
  newName = '';
  get validJunction(): boolean {
    return (
      /^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$/.test(this.newId.trim()) && this.newName.trim().length > 0
    );
  }
  open(): void {
    this.newId = '';
    this.newName = '';
    this.store.notice.set(null);
    this.nativeDialog().nativeElement.showModal();
  }
  async createJunction(): Promise<void> {
    if (!this.validJunction) return;
    const id = this.newId.trim();
    if (await this.store.createJunction(id, this.newName)) {
      this.nativeDialog().nativeElement.close();
      await this.router.navigate(['/junctions', id]);
    }
  }
}
