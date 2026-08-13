<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import AppShell from '@/components/AppShell.vue'
import { api } from '@/api/client'
import type { ResearchReportIndex } from '@/types/research'
import { formatDate } from '@/utils/format'

const reports = ref<ResearchReportIndex[]>([])
const loading = ref(true)
const error = ref('')
const latest = computed(() => reports.value.find((item) => ['succeeded', 'partial', 'technical_ready'].includes(item.status)) ?? reports.value[0] ?? null)

function reportLabel(report: ResearchReportIndex) {
  if (report.trigger_type === 'manual') return '手动 · 当前状态'
  if (report.decision_phase === 'post_close_review') return '正式 · 收盘复盘'
  return '正式 · 盘前决策'
}

function reportSource(report: ResearchReportIndex) {
  if (report.trading_date) return report.trading_date
  const parts = report.dataset_key.split(':')
  return parts.slice(0, 2).join(' · ')
}

onMounted(async () => {
  try {
    reports.value = await api.listAllResearchReports(20)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '研究报告列表加载失败。'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <AppShell />
  <main class="page-shell research-library-page">
    <section class="research-library-header">
      <div>
        <p class="eyebrow">RESEARCH CENTER</p>
        <h1>研究中心</h1>
        <p class="hero-copy">查看最新研究判断，或进入正式日循环、手动分析和历史报告。</p>
      </div>
      <div class="research-library-actions">
        <RouterLink class="primary-button" to="/analysis/new">手动分析</RouterLink>
        <RouterLink class="text-link" to="/research/daily">正式日循环</RouterLink>
        <RouterLink class="text-link" to="/research/on-demand">手动记录</RouterLink>
        <RouterLink class="text-link" to="/research/datasets">冻结数据集</RouterLink>
        <RouterLink class="text-link" to="/research/reports">全部历史 →</RouterLink>
        <RouterLink class="text-link" to="/operations">开发工具 →</RouterLink>
      </div>
    </section>

    <div v-if="error" class="error-banner" role="alert">{{ error }}</div>
    <section v-if="loading" class="empty-panel"><p>正在查找最新研究报告…</p></section>
    <section v-else-if="latest" class="report-entry-panel report-home-latest">
      <div>
        <div class="report-entry-meta">
          <span class="live-badge">{{ reportLabel(latest) }}</span>
          <span class="status-badge" :data-status="latest.status">{{ latest.status }}</span>
        </div>
        <p class="eyebrow">最近更新 · {{ formatDate(latest.cutoff_time) }}</p>
        <h2>{{ reportSource(latest) }}</h2>
        <p>{{ latest.quality?.status ?? '数据质量状态未知' }} · {{ latest.run_summary?.run_count ?? 0 }} 个工作流运行</p>
      </div>
      <RouterLink class="primary-button report-entry-link" :to="`/research/reports/${latest.report_id}`">打开报告 →</RouterLink>
    </section>
    <section v-else-if="!error" class="empty-panel">
      <p class="eyebrow">NO REPORT YET</p>
      <h2>还没有可用报告。</h2>
      <p>完成一次正式任务或手动分析后，报告会出现在这里。</p>
      <RouterLink class="secondary-button report-home-operations-link" to="/analysis/new">发起手动分析</RouterLink>
    </section>

  </main>
</template>
