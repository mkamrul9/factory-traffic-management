import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DashboardStore } from '../core/dashboard-store';
import { AuditEntry, titleCase } from '../core/models';
import { Icon } from '../shared/icon';

@Component({
  selector: 'app-activity-feed',
  imports: [DatePipe, FormsModule, Icon],
  templateUrl: './activity-feed.html',
  styleUrl: './activity-feed.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ActivityFeed {
  readonly store = inject(DashboardStore);
  readonly showAllHistory = signal(false);
  readonly activityFilter = signal('ALL');
  readonly visibleHistory = computed(() => {
    const filter = this.activityFilter();
    const entries = this.store
      .history()
      .filter(
        (entry) =>
          filter === 'ALL' ||
          (filter === 'TRAFFIC'
            ? /VEHICLE|EMERGENCY|SENSOR_EVENT/.test(entry.event_type)
            : filter === 'SAFETY'
              ? /FAILURE|TIMEOUT|REJECTED|RECOVER/.test(entry.event_type)
              : /MANUAL|MODE|AUTOMATIC|ACKNOWLEDGEMENT/.test(entry.event_type)),
      );
    return this.showAllHistory() ? entries : entries.slice(0, 7);
  });
  activityTitle(entry: AuditEntry): string {
    return titleCase(entry.event_type);
  }
  activityDetail(entry: AuditEntry): string {
    const details = entry.details;
    if (typeof details['message'] === 'string') return details['message'];
    const parts = [
      details['direction'],
      details['vehicle_id'],
      details['requested_state'] ?? details['actual_state'],
      details['phase_id'],
      details['reason'] ?? details['code'],
    ];
    return (
      parts
        .filter((part): part is string => typeof part === 'string' && part.length > 0)
        .join(' · ') ||
      entry.event_id ||
      'Junction state recorded'
    );
  }
  activityTone(entry: AuditEntry): string {
    return /FAILURE|TIMEOUT|REJECTED/.test(entry.event_type)
      ? 'warning'
      : /EMERGENCY/.test(entry.event_type)
        ? 'emergency'
        : /RECOVERED|CONFIRMED|ARRIVED/.test(entry.event_type)
          ? 'success'
          : 'neutral';
  }
  activityIcon(entry: AuditEntry): string {
    return /FAILURE|TIMEOUT|REJECTED|EMERGENCY/.test(entry.event_type)
      ? 'alert'
      : /VEHICLE/.test(entry.event_type)
        ? 'truck'
        : /SIGNAL|CONTROLLER/.test(entry.event_type)
          ? 'signal'
          : 'activity';
  }
}
