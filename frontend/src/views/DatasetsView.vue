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
const datasets = computed(() => {
  const groups = new Map<string, ResearchReportIndex[]>()
  for (const report of reports.value) {
    const entries = groups.get(report.dataset_key) ?? []
    entries.push(report)
    groups.set(report.dataset_key, entries)
  }
  return [...groups.entries()].map(([key, entries]) => ({ key, reports: entries, latest: entries[0] }))
})

onMounted(async () => {
  try {
    reports.value = await api.listAllResearchReports(100)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '数据集索引加载失败。'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <AppShell />
  <main class="page-shell narrow-page">
    <section class="page-intro">
      <p class="eyebrow">DECISION DATASETS</p>
      <h1>数据集</h1>
      <p>这里先按报告索引展示数据集；后续补充盘前、收盘前两个 Run 的质量审计。</p>
    </section>
    <div v-if="error" class="error-banner" role="alert">{{ error }}</div>
    <section v-if="loading" class="empty-panel"><p>正在读取数据集索引…</p></section>
    <section v-else-if="datasets.length" class="dataset-list">
      <article v-for="dataset in datasets" :key="dataset.key" class="report-card dataset-card">
        <div class="report-card-title"><strong>{{ dataset.key }}</strong><span>{{ dataset.reports.length }} report versions</span></div>
        <div class="dataset-facts"><span>最新截止 {{ formatDate(dataset.latest.cutoff_time) }}</span><span>状态 {{ dataset.latest.status }}</span><span>来源 run {{ dataset.latest.workflow_run_id }}</span></div>
        <RouterLink class="secondary-button" :to="`/research/reports/${dataset.latest.report_id}`">打开最新报告</RouterLink>
      </article>
    </section>
    <section v-else-if="!error" class="empty-panel"><p>还没有冻结数据集。</p></section>
  </main>
</template>
