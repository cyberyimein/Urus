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
  <main class="page-shell research-home-page">
    <section class="research-home-hero">
      <div>
        <p class="eyebrow">RESEARCH CENTER</p>
        <h1>研究中心</h1>
        <p class="hero-copy">正式日循环、手动即时分析和冻结数据集在这里分层归档。</p>
      </div>
      <div class="research-home-status">
        <span class="live-badge">不可变研究档案</span>
        <span class="subtle">主页负责现状判断与发起分析</span>
      </div>
    </section>

    <nav class="research-center-nav" aria-label="研究中心二级导航">
      <RouterLink to="/research/daily">今日正式报告</RouterLink>
      <RouterLink to="/research/on-demand">手动分析</RouterLink>
      <RouterLink to="/research/reports">全部历史</RouterLink>
      <RouterLink to="/research/datasets">数据集</RouterLink>
    </nav>

    <div v-if="error" class="error-banner" role="alert">{{ error }}</div>
    <section v-if="loading" class="empty-panel"><p>正在查找最新研究报告…</p></section>
    <section v-else-if="latest" class="report-entry-panel report-home-latest">
      <div>
        <p class="eyebrow">LATEST AVAILABLE REPORT</p>
        <h2>{{ formatDate(latest.cutoff_time) }} · {{ latest.status }}</h2>
        <p>数据集 {{ latest.dataset_key }} · {{ latest.quality?.status ?? 'quality unknown' }}</p>
      </div>
      <RouterLink class="primary-button report-entry-link" :to="`/research/reports/${latest.report_id}`">打开研究报告 →</RouterLink>
    </section>
    <section v-else-if="!error" class="empty-panel">
      <p class="eyebrow">NO REPORT YET</p>
      <h2>还没有可用报告。</h2>
      <p>先到开发工具完成一组采集，再回到这里查看冻结报告。</p>
      <RouterLink class="secondary-button report-home-operations-link" to="/operations">打开开发工具</RouterLink>
    </section>

    <section class="research-home-grid">
      <article class="report-card">
        <p class="eyebrow">TECHNICAL</p>
        <h3>技术整理报告</h3>
        <p>图表、热力格、题材矩阵和期权结构，程序生成，不混入 AI 观点。</p>
      </article>
      <article class="report-card">
        <p class="eyebrow">DECISION</p>
        <h3>AI 决策报告</h3>
        <p>只展示通过 Schema 和证据引用校验的结构化决策结果。</p>
      </article>
      <article class="report-card">
        <p class="eyebrow">REPLAY</p>
        <h3>AI 决策复盘</h3>
        <p>查看真实节点、工具调用、结构化理由，以及手动展开的模型原始返回。</p>
      </article>
    </section>
  </main>
</template>
