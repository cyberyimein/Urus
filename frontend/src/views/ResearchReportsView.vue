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
    <section v-if="loading" class="empty-panel"><p>正在读取报告索引…</p></section>
    <section v-else-if="visibleReports.length" class="runs-list report-history-list">
      <RouterLink v-for="report in visibleReports" :key="report.report_id" class="run-list-row" :to="`/research/reports/${report.report_id}`">
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
    </section>
    <section v-else-if="!error" class="empty-panel"><p>还没有历史报告。</p></section>
  </main>
</template>
