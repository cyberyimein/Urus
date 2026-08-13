<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import { api } from '@/api/client'
import AppShell from '@/components/AppShell.vue'
import type { RunDetail } from '@/types/api'
import type { ResearchReportIndex } from '@/types/research'
import { formatDate, statusLabel } from '@/utils/format'

const route = useRoute()
const run = ref<RunDetail | null>(null)
const report = ref<ResearchReportIndex | null>(null)
const error = ref('')
const retrying = ref(false)
let timer: number | undefined

const runId = computed(() => String(route.params.runId))
const terminal = computed(() => run.value ? !['pending', 'running'].includes(run.value.status) : false)
const reportReady = computed(() => Boolean(report.value && ['succeeded', 'partial'].includes(report.value.status)))
const runFailed = computed(() => run.value?.status === 'failed')
const displayStatus = computed(() => reportReady.value ? 'succeeded' : (run.value?.status ?? 'pending'))
const displaySteps = computed(() => (run.value?.steps ?? []).map((step) => (
  reportReady.value && step.step_code === '4'
    ? { ...step, status: 'succeeded', summary: 'AI 现状分析重试成功，最新报告已保存。', error_message: null }
    : step
)))
const aiFailed = computed(() => Boolean(
  report.value && ['failed', 'timed_out'].includes(report.value.status),
))
const aiFailureMessage = computed(() => {
  const aiStep = run.value?.steps.find((step) => step.step_code === '4')
  return aiStep?.error_message || 'AI 现状分析未完成，已采集的数据与技术报告仍然保留。'
})
const elapsed = computed(() => {
  if (!run.value?.started_at) return '等待启动'
  const end = run.value.completed_at ? new Date(run.value.completed_at).getTime() : Date.now()
  return `${Math.max(0, Math.round((end - new Date(run.value.started_at).getTime()) / 1000))} 秒`
})

const stepLabels: Record<string, string> = {
  '1a': '大盘与跨资产行情', '1b': 'CTA 市场压力', '2': '期权结构',
  '3a': '个股与 ETF', '3b': '系统化资金压力', '4': 'AI 现状分析', '5': '技术报告与冻结输出',
}

async function refresh() {
  try {
    run.value = await api.getRun(runId.value)
    const reports = await api.listResearchReports(runId.value)
    report.value = reports[0] ?? null
    if (!terminal.value) timer = window.setTimeout(refresh, 1800)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '分析进度加载失败。'
  }
}

async function retryAi() {
  retrying.value = true
  error.value = ''
  const previousReportId = report.value?.report_id
  try {
    await api.retryManualAnalysisAi(runId.value)
    const pollRetry = async () => {
      const reports = await api.listResearchReports(runId.value)
      report.value = reports[0] ?? null
      const isNew = Boolean(report.value && report.value.report_id !== previousReportId)
      if (isNew && report.value && !['running', 'waiting_for_pair'].includes(report.value.status)) {
        retrying.value = false
        return
      }
      timer = window.setTimeout(pollRetry, 1800)
    }
    timer = window.setTimeout(pollRetry, 800)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : 'AI 重试失败。'
    retrying.value = false
  }
}

onMounted(refresh)
onBeforeUnmount(() => { if (timer) window.clearTimeout(timer) })
</script>

<template>
  <AppShell />
  <main class="page-shell analysis-progress-page">
    <RouterLink class="back-link" to="/">← 返回首页</RouterLink>
    <section class="analysis-progress-header">
      <div><p class="eyebrow">MANUAL CURRENT-STATE ANALYSIS</p><h1>正在分析当前市场</h1><p>{{ run ? formatDate(run.cutoff_time) : '正在创建冻结边界…' }}</p></div>
      <div class="analysis-progress-meta"><span class="status-badge" :data-status="displayStatus">{{ statusLabel(displayStatus) }}</span><small>已耗时 {{ elapsed }}</small></div>
    </section>
    <div v-if="error" class="error-banner" role="alert">{{ error }}</div>

    <section v-if="run" class="analysis-step-list">
      <article v-for="step in displaySteps" :key="step.id" :data-status="step.status">
        <span class="analysis-step-icon">{{ step.status === 'succeeded' ? '✓' : step.status === 'running' ? '●' : ['failed', 'unavailable'].includes(step.status) ? '!' : '○' }}</span>
        <div><strong>{{ stepLabels[step.step_code] }}</strong><small>{{ step.summary || statusLabel(step.status) }}</small><em v-if="step.error_message">{{ step.error_message }}</em></div>
        <span>{{ statusLabel(step.status) }}</span>
      </article>
    </section>

    <section v-if="terminal" class="analysis-complete-card" :data-status="displayStatus">
      <div>
        <p class="eyebrow">{{ reportReady ? 'REPORT READY' : aiFailed ? 'PARTIAL RESULT' : 'COLLECTION FINISHED' }}</p>
        <h2>{{ reportReady ? '技术报告与 AI 现状分析已保存' : report ? '技术报告已完成，AI 现状分析失败' : runFailed ? '手动分析未生成报告' : '本轮数据已冻结' }}</h2>
        <p v-if="aiFailed" class="analysis-failure-message">{{ aiFailureMessage }}</p>
        <p v-else-if="run?.error_message" class="analysis-failure-message">{{ run.error_message }}</p>
        <p v-else-if="!report">AI 未生成可阅读报告，但已完成的数据和错误仍保留在本轮快照中。</p>
      </div>
      <div class="button-row analysis-result-actions">
        <RouterLink v-if="report" class="primary-button" :to="`/research/reports/${report.report_id}`">{{ reportReady ? '打开手动报告' : '查看技术报告' }}</RouterLink>
        <button v-if="!reportReady" class="secondary-button" type="button" :disabled="retrying" @click="retryAi">{{ retrying ? 'AI 正在重试…' : '重新运行 AI' }}</button>
        <RouterLink class="secondary-button" :to="`/operations/runs/${runId}`">查看采集详情</RouterLink>
        <RouterLink class="text-link" to="/analysis/new">再运行一次 →</RouterLink>
      </div>
    </section>
  </main>
</template>
