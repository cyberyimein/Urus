import { createRouter, createWebHistory } from 'vue-router'

import DashboardView from '@/views/DashboardView.vue'
import OptionsView from '@/views/OptionsView.vue'
import RunDetailView from '@/views/RunDetailView.vue'
import RunsView from '@/views/RunsView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: DashboardView },
    { path: '/options', name: 'options', component: OptionsView },
    { path: '/runs', name: 'runs', component: RunsView },
    { path: '/runs/:runId', name: 'run-detail', component: RunDetailView },
  ],
})
