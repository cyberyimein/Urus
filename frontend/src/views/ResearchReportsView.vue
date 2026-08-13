<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import AppShell from '@/components/AppShell.vue'
import { api } from '@/api/client'
import type { ResearchReportIndex } from '@/types/research'
import { formatDate } from '@/utils/format'

const reports = ref<ResearchReportIndex[]>([])
const props = withDefaults(defineProps<{ mode?: 'all' | 'daily' | 'manual' }>(), { mode: 'all' })
const loading = ref(true)
const error = ref('')
const deleteError = ref('')
const pendingDeleteId = ref('')
const deletingId = ref('')
const latestFormalDate = computed(() => reports.value.find((report) => report.official_cycle !== false)?.trading_date)
const visibleReports = computed(() => reports.value.filter((report) => (
  props.mode === 'manual' ? report.trigger_type === 'manual'
    : props.mode === 'daily' ? report.official_cycle !== false && report.trading_date === latestFormalDate.value
      : true
)))
const title = computed(() => props.mode === 'manual'
  ? '手动即时分析'
  : props.mode === 'daily'
    ? `最新正式日循环${latestFormalDate.value ? ` · ${latestFormalDate.value}` : ''}`
    : '历史报告')

function requestDelete(reportId: string) {
  deleteError.value = ''
  pendingDeleteId.value = pendingDeleteId.value === reportId ? '' : reportId
}

function cancelDelete() {
  pendingDeleteId.value = ''
}

async function deleteReport(report: ResearchReportIndex) {
  if (deletingId.value) return
  deleteError.value = ''
  deletingId.value = report.report_id
  try {
    await api.deleteResearchReport(report.report_id)
    reports.value = reports.value.filter((item) => item.report_id !== report.report_id)
    pendingDeleteId.value = ''
  } catch (reason) {
    deleteError.value = reason instanceof Error ? reason.message : '报告删除失败，请稍后重试。'
  } finally {
    deletingId.value = ''
  }
}

onMounted(async () => {
  try {
    reports.value = await api.listAllResearchReports(100)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '历史报告加载失败。'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <AppShell />
  <main class="page-shell narrow-page">
    <section class="page-intro">
      <p class="eyebrow">RESEARCH REPORTS</p>
      <h1>{{ title }}</h1>
      <p>报告是不可变版本；手动分析与正式日循环保持独立。</p>
    </section>
    <div v-if="error" class="error-banner" role="alert">{{ error }}</div>
    <div v-if="deleteError" class="error-banner" role="alert">{{ deleteError }}</div>
    <section v-if="loading" class="empty-panel"><p>正在读取报告索引…</p></section>
    <section v-else-if="visibleReports.length" class="runs-list report-history-list">
      <article v-for="report in visibleReports" :key="report.report_id" class="run-list-row report-history-row">
        <RouterLink class="report-history-link" :to="`/research/reports/${report.report_id}`">
          <div>
            <span class="eyebrow">{{ formatDate(report.cutoff_time) }} · {{ report.dataset_key }}</span>
            <strong>{{ report.trigger_type === 'manual' ? '手动 · 当前状态' : report.decision_phase === 'post_close_review' ? '正式 · 收盘复盘' : '正式 · 盘前决策' }}</strong>
            <small>{{ report.report_id }}</small>
          </div>
          <div class="run-row-meta">
            <span class="status-badge" :data-status="report.status">{{ report.status }}</span>
            <span class="subtle">{{ report.run_summary?.run_count ?? 0 }} runs</span>
          </div>
        </RouterLink>
        <div class="report-row-actions">
          <template v-if="pendingDeleteId === report.report_id">
            <span class="delete-confirm-copy">删除报告及 AI 轨迹，不删除采集数据</span>
            <button type="button" class="secondary-button" :disabled="deletingId === report.report_id" @click="cancelDelete">取消</button>
            <button type="button" class="danger-button" :disabled="deletingId === report.report_id" @click="deleteReport(report)">{{ deletingId === report.report_id ? '删除中…' : '确认删除' }}</button>
          </template>
          <button v-else type="button" class="report-delete-button" :disabled="report.status === 'running'" :title="report.status === 'running' ? '报告生成中，暂不能删除' : '删除这份历史报告'" @click="requestDelete(report.report_id)">删除</button>
        </div>
      </article>
    </section>
    <section v-else-if="!error" class="empty-panel"><p>还没有历史报告。</p></section>
  </main>
</template>
