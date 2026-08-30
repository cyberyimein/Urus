import { createRouter, createWebHistory } from 'vue-router'

import DashboardView from '@/views/DashboardView.vue'
import ManualAnalysisNewView from '@/views/ManualAnalysisNewView.vue'
import ManualAnalysisRunView from '@/views/ManualAnalysisRunView.vue'
import DatasetsView from '@/views/DatasetsView.vue'
import OperationsView from '@/views/OperationsView.vue'
import RunDetailView from '@/views/RunDetailView.vue'
import RunsView from '@/views/RunsView.vue'
import ResearchHomeView from '@/views/ResearchHomeView.vue'
import ResearchReportsView from '@/views/ResearchReportsView.vue'
import ResearchReportView from '@/views/ResearchReportView.vue'
import UniverseSettingsView from '@/views/UniverseSettingsView.vue'
import InstrumentDecisionView from '@/views/InstrumentDecisionView.vue'
import GroupObservationView from '@/views/GroupObservationView.vue'
import ObservationRunView from '@/views/ObservationRunView.vue'
import CrossSectionView from '@/views/CrossSectionView.vue'
import RemoteDecisionRunView from '@/views/RemoteDecisionRunView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: InstrumentDecisionView },
    { path: '/instruments/:symbol', name: 'instrument-decision', component: InstrumentDecisionView },
    { path: '/groups', name: 'groups', component: GroupObservationView },
    { path: '/groups/:groupId', name: 'group-observation', component: GroupObservationView },
    // Keep the product-facing route aligned with the Phase D design while
    // accepting the API-oriented path used by early clients/bookmarks.
    { path: '/decision-runs/:localRunId', alias: '/remote-decisions/:localRunId', name: 'remote-decision-run', component: RemoteDecisionRunView },
    { path: '/observation-runs', name: 'observation-runs', component: ObservationRunView },
    { path: '/indicators', name: 'indicators', component: CrossSectionView, props: { lensType: 'indicator' } },
    { path: '/indicators/:indicatorId', name: 'indicator-cross-section', component: CrossSectionView, props: { lensType: 'indicator' } },
    { path: '/strategies', name: 'strategies', component: CrossSectionView, props: { lensType: 'strategy' } },
    { path: '/strategies/:strategyId', name: 'strategy-cross-section', component: CrossSectionView, props: { lensType: 'strategy' } },
    { path: '/analysis/new', name: 'manual-analysis-new', component: ManualAnalysisNewView },
    { path: '/analysis/runs/:runId', name: 'manual-analysis-run', component: ManualAnalysisRunView },
    { path: '/research', name: 'research', component: ResearchHomeView },
    { path: '/research/daily', name: 'research-daily', component: ResearchReportsView, props: { mode: 'daily' } },
    { path: '/research/on-demand', name: 'research-on-demand', component: ResearchReportsView, props: { mode: 'manual' } },
    { path: '/research/reports', name: 'research-reports', component: ResearchReportsView },
    { path: '/research/reports/:reportId', name: 'research-report-by-id', component: ResearchReportView },
    { path: '/research/datasets', name: 'research-datasets', component: DatasetsView },
    { path: '/operations', name: 'operations', component: OperationsView },
    // The legacy runtime settings page exposed the retired scheduled AI/report
    // controls. Keep the old URL as a safe bookmark redirect to the active
    // Universe settings surface; runtime credentials and Workflow bindings
    // remain deployment-managed rather than user-editable here.
    { path: '/settings', redirect: { name: 'universe-settings' } },
    { path: '/settings/universe', name: 'universe-settings', component: UniverseSettingsView },
    { path: '/operations/runs', name: 'runs', component: RunsView },
    { path: '/operations/runs/:runId', name: 'run-detail', component: RunDetailView },
    { path: '/runs/:runId/report', name: 'research-report', component: ResearchReportView },
    { path: '/research-reports/:reportId', redirect: (to) => ({ name: 'research-report-by-id', params: { reportId: to.params.reportId }, query: to.query, hash: to.hash }) },
    { path: '/runs', redirect: { name: 'runs' } },
    { path: '/runs/:runId', redirect: (to) => ({ name: 'run-detail', params: { runId: to.params.runId }, query: to.query, hash: to.hash }) },
    // Options are rendered in the selected instrument workspace now. Keep the
    // old URL as a safe bookmark redirect without exposing a standalone page.
    { path: '/options', redirect: (to) => typeof to.query.symbol === 'string'
      ? { name: 'instrument-decision', params: { symbol: to.query.symbol } }
      : { name: 'home' } },
  ],
})
