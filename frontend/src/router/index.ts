import { createRouter, createWebHistory } from 'vue-router'

import UniverseSettingsView from '@/views/UniverseSettingsView.vue'
import InstrumentDecisionView from '@/views/InstrumentDecisionView.vue'
import GroupObservationView from '@/views/GroupObservationView.vue'
import ObservationRunView from '@/views/ObservationRunView.vue'
import IndicatorCrossSectionView from '@/views/IndicatorCrossSectionView.vue'
import StrategyCrossSectionView from '@/views/StrategyCrossSectionView.vue'
import RemoteDecisionRunView from '@/views/RemoteDecisionRunView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: InstrumentDecisionView },
    { path: '/instruments/:symbol', name: 'instrument-decision', component: InstrumentDecisionView },
    { path: '/groups', name: 'groups', component: GroupObservationView },
    { path: '/groups/:groupId', name: 'group-observation', component: GroupObservationView },
    // Keep the product-facing route readable while accepting the API-oriented
    // path used by existing clients and bookmarks.
    { path: '/decision-runs/:localRunId', alias: '/remote-decisions/:localRunId', name: 'remote-decision-run', component: RemoteDecisionRunView },
    { path: '/observation-runs', name: 'observation-runs', component: ObservationRunView },
    // Indicator and strategy surfaces intentionally use different route
    // components. They share the visual implementation, but must not share a
    // mounted component tree when moving between the two workspaces.
    { path: '/indicators', name: 'indicators', component: IndicatorCrossSectionView },
    { path: '/indicators/:indicatorId', name: 'indicator-cross-section', component: IndicatorCrossSectionView },
    { path: '/strategies', name: 'strategies', component: StrategyCrossSectionView },
    { path: '/strategies/:strategyId', name: 'strategy-cross-section', component: StrategyCrossSectionView },
    // Keep the settings entry point focused on the active Universe surface.
    { path: '/settings', redirect: { name: 'universe-settings' } },
    { path: '/settings/universe', name: 'universe-settings', component: UniverseSettingsView },
    // Options are rendered in the selected instrument workspace; this redirect
    // keeps the single-surface information architecture intact.
    { path: '/options', redirect: (to) => typeof to.query.symbol === 'string'
      ? { name: 'instrument-decision', params: { symbol: to.query.symbol } }
      : { name: 'home' } },
  ],
})
