import { createRouter, createWebHistory } from 'vue-router'

import DashboardView from '@/views/DashboardView.vue'
import HomeView from '@/views/HomeView.vue'
import ManualAnalysisNewView from '@/views/ManualAnalysisNewView.vue'
import ManualAnalysisRunView from '@/views/ManualAnalysisRunView.vue'
import DatasetsView from '@/views/DatasetsView.vue'
import OperationsView from '@/views/OperationsView.vue'
import RunDetailView from '@/views/RunDetailView.vue'
import RunsView from '@/views/RunsView.vue'
import ResearchHomeView from '@/views/ResearchHomeView.vue'
import ResearchReportsView from '@/views/ResearchReportsView.vue'
import ResearchReportView from '@/views/ResearchReportView.vue'
import SettingsView from '@/views/SettingsView.vue'
import UniverseSettingsView from '@/views/UniverseSettingsView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    { path: '/analysis/new', name: 'manual-analysis-new', component: ManualAnalysisNewView },
    { path: '/analysis/runs/:runId', name: 'manual-analysis-run', component: ManualAnalysisRunView },
    { path: '/research', name: 'research', component: ResearchHomeView },
    { path: '/research/daily', name: 'research-daily', component: ResearchReportsView, props: { mode: 'daily' } },
    { path: '/research/on-demand', name: 'research-on-demand', component: ResearchReportsView, props: { mode: 'manual' } },
    { path: '/research/reports', name: 'research-reports', component: ResearchReportsView },
    { path: '/research/reports/:reportId', name: 'research-report-by-id', component: ResearchReportView },
    { path: '/research/datasets', name: 'research-datasets', component: DatasetsView },
    { path: '/operations', name: 'operations', component: OperationsView },
    { path: '/settings', name: 'settings', component: SettingsView },
    { path: '/settings/universe', name: 'universe-settings', component: UniverseSettingsView },
    { path: '/operations/runs', name: 'runs', component: RunsView },
    { path: '/operations/runs/:runId', name: 'run-detail', component: RunDetailView },
    { path: '/runs/:runId/report', name: 'research-report', component: ResearchReportView },
    { path: '/research-reports/:reportId', redirect: (to) => ({ name: 'research-report-by-id', params: { reportId: to.params.reportId }, query: to.query, hash: to.hash }) },
    { path: '/runs', redirect: { name: 'runs' } },
    { path: '/runs/:runId', redirect: (to) => ({ name: 'run-detail', params: { runId: to.params.runId }, query: to.query, hash: to.hash }) },
    { path: '/options', redirect: { name: 'operations', query: { tab: 'options' } } },
  ],
})
