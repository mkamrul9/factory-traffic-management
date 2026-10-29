import { Routes } from '@angular/router';
import { Dashboard } from './dashboard/dashboard';

export const routes: Routes = [
  { path: '', redirectTo: 'junctions/A', pathMatch: 'full' },
  { path: 'junctions/:junctionId', component: Dashboard },
  { path: '**', redirectTo: 'junctions/A' },
];
