import { ChangeDetectionStrategy, Component, input } from '@angular/core';

const PATHS: Record<string, string> = {
  grid: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
  road: 'M8 3 5 21M16 3l3 18M12 3v3m0 4v4m0 4v3',
  activity: 'M3 12h4l3-8 4 16 3-8h4',
  clock: 'M12 8v4l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
  settings: 'M4 7h16M4 17h16M8 4v6M16 14v6',
  chevron: 'm9 5 7 7-7 7',
  down: 'm6 9 6 6 6-6',
  refresh: 'M20 7v5h-5M4 17v-5h5M6 6a8 8 0 0 1 13 1M5 17a8 8 0 0 0 13 1',
  plus: 'M12 5v14M5 12h14',
  alert: 'm12 3 10 18H2L12 3zM12 9v4m0 3v1',
  check: 'm5 12 4 4L19 6',
  close: 'm6 6 12 12M6 18 18 6',
  truck:
    'M3 6h11v11H3zM14 10h4l3 4v3h-7M7 17a2 2 0 1 0 0 4 2 2 0 0 0 0-4m11 0a2 2 0 1 0 0 4 2 2 0 0 0 0-4',
  signal: 'M8 2h8v16H8zM12 18v4M11 5h2m-2 5h2m-2 5h2',
  shield: 'm12 2 8 4v6c0 5-8 10-8 10S4 17 4 12V6l8-4zM9 12l2 2 4-4',
  play: 'm8 4 12 8-12 8V4z',
  arrow: 'M12 20V4m-6 6 6-6 6 6',
  menu: 'M4 6h16M4 12h16M4 18h16',
  external: 'M14 3h7v7m0-7L10 14M10 3H3v18h18v-7',
  chip: 'M6 6h12v12H6zM9 9h6v6H9zM9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4',
};

@Component({
  selector: 'app-icon',
  template:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path [attr.d]="path()" /></svg>',
  styles: [
    ':host { display:inline-flex; width:20px; height:20px; flex:none; } svg { width:100%; height:100%; }',
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class Icon {
  readonly name = input.required<string>();
  path(): string {
    return PATHS[this.name()] ?? PATHS['grid'];
  }
}
