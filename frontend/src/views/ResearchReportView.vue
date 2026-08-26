<script setup lang="ts">
import { computed, nextTick, onMounted, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import AppShell from '@/components/AppShell.vue'
import DecisionReportTab from '@/components/research/DecisionReportTab.vue'
import DecisionTraceTab from '@/components/research/DecisionTraceTab.vue'
import ReportHeader from '@/components/research/ReportHeader.vue'
import TechnicalReportTab from '@/components/research/TechnicalReportTab.vue'
import { useResearchReportStore } from '@/stores/researchReport'
import type { TechnicalSection } from '@/types/research'

const route = useRoute()
const router = useRouter()
const store = useResearchReportStore()
const runId = computed(() => String(route.params.runId ?? ''))
const reportId = computed(() => String(route.params.reportId ?? ''))
const queryReportId = computed(() => String(route.query.report ?? ''))
const queryTab = computed(() => {
  const value = String(route.query.tab ?? 'technical')
  return value === 'decision' || value === 'review' || value === 'trace' ? value : 'technical'
})
const queryTechnicalSection = computed<TechnicalSection>(() => {
  const value = String(route.query.section ?? 'overview')
  return ['overview', 'instruments', 'options', 'events'].includes(value) ? value as TechnicalSection : 'overview'
})
const querySymbol = computed(() => {
  const value = String(route.query.symbol ?? '').trim().toUpperCase()
  return value || ''
})

function storeTab(value: string): 'technical' | 'decision' | 'trace' {
  return value === 'decision' ? 'decision' : value === 'review' || value === 'trace' ? 'trace' : 'technical'
}

async function load() {
  store.activeTab = storeTab(queryTab.value)
  if (reportId.value) await store.loadReport(reportId.value)
  else if (runId.value) {
    await store.loadForRun(runId.value, queryReportId.value)
    // Old run-scoped links remain readable during migration, but a persisted
    // report should immediately settle on its report_id canonical URL.
    if (store.report?.report_id && !store.report.report_id.startsWith('disabled-')) {
      await router.replace({ name: 'research-report-by-id', params: { reportId: store.report.report_id }, query: route.query, hash: route.hash })
    }
  }
}

onMounted(() => void load())
watch([runId, reportId, queryReportId], () => void load())
watch(queryTab, (value) => {
  if (store.report) void store.selectTab(storeTab(value))
})

function selectTab(tab: 'technical' | 'decision' | 'trace') {
  const urlTab = tab === 'trace' ? 'review' : tab
  void store.selectTab(tab)
  void router.replace({ query: { ...route.query, tab: urlTab } })
}

function selectTechnicalSection(section: TechnicalSection) {
  void router.replace({ query: { ...route.query, tab: 'technical', section } })
}

function technicalSectionForEvidence(path: string): TechnicalSection {
  const normalized = path.toLowerCase()
  if (normalized.includes('option')) return 'options'
  if (normalized.includes('event')) return 'events'
  if (normalized.includes('instrument') || normalized.includes('symbol') || normalized.includes('theme')) return 'instruments'
  return 'overview'
}

function selectReport(id: string) {
  void router.replace({ query: { ...route.query, report: id } })
}

function selectSymbol(symbol: string) {
  void router.replace({ query: { ...route.query, tab: 'decision', symbol } })
}

function closeSymbol() {
  const query = { ...route.query }
  delete query.symbol
  void router.replace({ query })
}

async function focusEvidence(path: string) {
  if (!path) return
  const prefixes = path.split('.').map((_part, index, parts) => parts.slice(0, parts.length - index).join('.'))
  const safe = prefixes[0].replace(/[^a-zA-Z0-9_-]+/g, '-').replace(/^-|-$/g, '').toLowerCase()
  await router.replace({ query: { ...route.query, tab: 'technical', section: technicalSectionForEvidence(path) }, hash: `#evidence-${safe}` })
  await nextTick()
  const target = prefixes
    .map((candidate) => candidate.replace(/[^a-zA-Z0-9_-]+/g, '-').replace(/^-|-$/g, '').toLowerCase())
    .map((candidate) => document.getElementById(`evidence-${candidate}`))
    .find(Boolean)
  target?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  target?.classList.add('evidence-focus')
  window.setTimeout(() => target?.classList.remove('evidence-focus'), 1800)
}
</script>

<template>
  <AppShell />
  <main class="page-shell research-page">
    <RouterLink class="back-link" :to="runId ? `/operations/runs/${runId}` : '/research/reports'">← {{ runId ? '返回开发工具' : '返回旧研究中心 · 历史报告' }}</RouterLink>
    <div v-if="store.error" class="error-banner" role="alert">{{ store.error }}</div>
    <div v-if="store.loading && !store.report" class="empty-panel"><p>正在载入 Stage 4B 研究会话…</p></div>
    <template v-else-if="store.report">
      <ReportHeader
        :report="store.report"
        :available="store.available"
        :active-tab="store.activeTab"
        @select-tab="selectTab"
        @select-report="selectReport"
      />
      <section class="research-report-body">
        <TechnicalReportTab
          v-if="store.activeTab === 'technical'"
          :report="store.technical"
          :report-id="store.report.report_id"
          :active-section="queryTechnicalSection"
          @select-section="selectTechnicalSection"
        />
        <DecisionReportTab
          v-else-if="store.activeTab === 'decision'"
          :report="store.decision"
          :technical="store.technical"
          :status="store.report.status"
          :error-message="store.report.error_message || store.error"
          :selected-symbol="querySymbol"
          @focus-evidence="focusEvidence"
          @select-symbol="selectSymbol"
          @close-symbol="closeSymbol"
        />
        <DecisionTraceTab
          v-else
          :trace="store.trace"
          :selected-node="store.selectedNode"
          :raw-response="store.rawResponse"
          :loading="store.nodeLoading"
          :raw-error="store.rawError"
          :status="store.report.status"
          :error-message="store.report.error_message"
          @select-node="store.selectNode"
          @load-raw="store.loadRawResponse"
          @focus-evidence="focusEvidence"
        />
      </section>
    </template>
    <section v-else-if="!store.error" class="empty-panel"><p>这个运行还没有 Stage 4B 研究报告。</p></section>
  </main>
</template>
