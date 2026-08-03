import { createRouter, createWebHistory } from 'vue-router'

import DashboardView from '@/views/DashboardView.vue'
import RunDetailView from '@/views/RunDetailView.vue'
import RunsView from '@/views/RunsView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: DashboardView },
    { path: '/options', redirect: { path: '/', query: { tab: 'options' } } },
    { path: '/runs', name: 'runs', component: RunsView },
    { path: '/runs/:runId', name: 'run-detail', component: RunDetailView },
  ],
})
