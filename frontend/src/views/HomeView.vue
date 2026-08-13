<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { api } from '@/api/client'
import AppShell from '@/components/AppShell.vue'
import type { FrontendReadModel, RunListItem } from '@/types/api'
import type { DecisionReport, ResearchReportIndex } from '@/types/research'
import { formatDate, nullable, statusLabel } from '@/utils/format'
import { buildMarketClock, type MarketClockState } from '@/utils/marketClock'

const loading = ref(true)
const error = ref('')
const runs = ref<RunListItem[]>([])
const reports = ref<ResearchReportIndex[]>([])
const readModel = ref<FrontendReadModel | null>(null)
const formalDecision = ref<DecisionReport | null>(null)
const marketClock = ref<MarketClockState>(buildMarketClock(new Date()))
let marketClockTimer: number | undefined

const formalReports = computed(() => reports.value.filter((item) => item.official_cycle !== false))
const manualReports = computed(() => reports.value.filter((item) => item.trigger_type === 'manual'))
const latestFormal = computed(() => formalReports.value.find((item) => ['succeeded', 'partial'].includes(item.status)) ?? null)
const latestSnapshotRun = computed(() => runs.value.find((item) => item.snapshot_id) ?? null)

const marketRegime = computed(() => {
  const regime = formalDecision.value?.market_regime
  return regime && typeof regime === 'object'
    ? nullable((regime as Record<string, unknown>).classification, '等待正式判断')
    : '等待正式判断'
})

const coreJudgment = computed(() => {
  const regime = formalDecision.value?.market_regime as Record<string, unknown> | undefined
  const marketOutput = (formalDecision.value?.market_analysis as Record<string, any> | undefined)?.output
  return nullable(
    regime?.summary ?? regime?.thesis ?? marketOutput?.forecast?.expected_path ?? marketOutput?.review?.session_summary,
    '正式报告尚未提供市场摘要；可以运行一次手动即时分析了解当前状态。',
  )
})

const systematicFlows = computed(() => readModel.value?.systematic_flows as Record<string, any> | undefined)
const ctaState = computed(() => {
  const portfolio = systematicFlows.value?.portfolio
  if (!systematicFlows.value?.available || !portfolio) return '数据不足'
  const net = Number(portfolio.unweighted_net_exposure)
  if (!Number.isFinite(net)) return '已更新'
  return `${net >= 0 ? '净多' : '净空'} ${Math.abs(net).toFixed(2)}`
})

const volatilityState = computed(() => {
  const symbols = readModel.value?.options?.symbols ?? []
  const observations = symbols
    .map((item) => ({ symbol: item.symbol, overview: item.overview as Record<string, unknown> }))
    .filter((item) => typeof item.overview?.iv_hv_ratio === 'number')
    .sort((a, b) => Number(a.overview.iv_hv_ratio) - Number(b.overview.iv_hv_ratio))
  const first = observations[0]
  return first ? `${first.symbol} · ${nullable(first.overview.iv_hv_regime)}` : '等待期权快照'
})

const attention = computed(() => {
  const messages: string[] = []
  const assets = systematicFlows.value?.assets ?? []
  for (const item of assets) {
    if (Math.abs(Number(item.pressure_index ?? 0)) >= 50) {
      messages.push(`${item.symbol} CTA 边际压力 ${Number(item.pressure_index).toFixed(0)}`)
    }
  }
  for (const item of readModel.value?.options?.symbols ?? []) {
    const overview = item.overview as Record<string, unknown>
    if (Number(overview?.iv_hv_ratio) < 0.7) messages.push(`${item.symbol} IV/HV 显著折价`)
  }
  for (const warning of readModel.value?.data_quality?.warnings ?? []) messages.push(String(warning))
  return messages.slice(0, 5)
})

onMounted(async () => {
  marketClockTimer = window.setInterval(() => {
    marketClock.value = buildMarketClock(new Date())
  }, 1000)
  try {
    ;[runs.value, reports.value] = await Promise.all([api.listRuns(), api.listAllResearchReports(50)])
    if (latestSnapshotRun.value?.snapshot_id) {
      readModel.value = await api.getFrontendReadModel(latestSnapshotRun.value.snapshot_id)
    }
    if (latestFormal.value) {
      formalDecision.value = await api.getDecisionReport(latestFormal.value.report_id).catch(() => null)
    }
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '主页数据加载失败。'
  } finally {
    loading.value = false
  }
})

onUnmounted(() => {
  if (marketClockTimer !== undefined) window.clearInterval(marketClockTimer)
})
</script>

<template>
  <AppShell />
  <main class="page-shell home-dashboard">
    <section class="home-command-bar">
      <div>
        <p class="eyebrow">CURRENT MARKET COMMAND</p>
        <h1>{{ marketClock.headline }}</h1>
        <p>数据截至 {{ latestSnapshotRun ? formatDate(latestSnapshotRun.cutoff_time) : '尚无快照' }} · 下一次正式任务：收盘后复盘</p>
      </div>
      <div class="home-analyze-cta">
        <RouterLink class="primary-button home-analyze-button" to="/analysis/new">打开手动分析</RouterLink>
        <small>进入确认页，不会立即采集</small>
      </div>
    </section>

    <section class="market-clock" :data-session="marketClock.session" aria-label="美东交易时间">
      <div class="market-clock-copy">
        <div class="market-clock-kicker"><span class="market-clock-pulse" aria-hidden="true"></span> NYSE / NASDAQ</div>
        <div class="market-clock-date">{{ marketClock.easternDate }} · {{ marketClock.sessionDetail }}</div>
        <div class="market-clock-time">{{ marketClock.easternTime }} <span>ET</span></div>
      </div>
      <div class="market-clock-countdown">
        <span class="market-clock-label">{{ marketClock.countdownLabel }}</span>
        <strong>{{ marketClock.countdown }}</strong>
        <small>{{ marketClock.targetLabel }}</small>
      </div>
      <div class="market-clock-track" aria-hidden="true">
        <span :style="{ width: `${marketClock.progress}%` }"></span>
      </div>
    </section>

    <div v-if="error" class="error-banner" role="alert">{{ error }}</div>
    <section v-if="loading" class="empty-panel"><p>正在组合最新正式判断与市场快照…</p></section>
    <template v-else>
      <section class="home-status-grid">
        <article><span>市场状态</span><strong>{{ marketRegime }}</strong><small>最新正式 AI</small></article>
        <article><span>CTA 压力</span><strong>{{ ctaState }}</strong><small>{{ nullable(systematicFlows?.model_state, '暂无模型状态') }}</small></article>
        <article><span>波动率定价</span><strong>{{ volatilityState }}</strong><small>综合 IV / HV30</small></article>
        <article><span>数据质量</span><strong>{{ statusLabel(readModel?.data_quality.status ?? 'unavailable') }}</strong><small>{{ readModel?.data_quality.errors.length ?? 0 }} 个错误</small></article>
      </section>

      <section class="home-main-grid">
        <article class="home-judgment-card">
          <p class="eyebrow">TODAY'S FORMAL VIEW</p>
          <h2>今日核心判断</h2>
          <p class="home-judgment-copy">{{ coreJudgment }}</p>
          <div class="button-row">
            <RouterLink v-if="latestFormal" class="secondary-button" :to="`/research/reports/${latestFormal.report_id}`">查看最新正式报告</RouterLink>
            <RouterLink class="text-link" to="/research/daily">今日正式报告 →</RouterLink>
          </div>
        </article>
        <article class="home-attention-card">
          <p class="eyebrow">ATTENTION</p>
          <h2>需要关注</h2>
          <ul v-if="attention.length" class="home-attention-list"><li v-for="item in attention" :key="item">{{ item }}</li></ul>
          <p v-else class="subtle">当前没有达到展示阈值的 CTA、IV/HV 或质量警告。</p>
        </article>
      </section>

      <section class="home-recent-section">
        <div class="section-title-row"><div><p class="eyebrow">RECENT RESEARCH</p><h2>最近报告</h2></div><RouterLink class="text-link" to="/research/reports">查看全部 →</RouterLink></div>
        <div class="home-report-strip">
          <RouterLink v-for="report in reports.slice(0, 3)" :key="report.report_id" class="report-card" :to="`/research/reports/${report.report_id}`">
            <span class="live-badge">{{ report.trigger_type === 'manual' ? '手动 · 即时分析' : report.decision_phase === 'post_close_review' ? '正式 · 收盘复盘' : '正式 · 盘前决策' }}</span>
            <strong>{{ formatDate(report.cutoff_time) }}</strong>
            <small>{{ report.status }} · {{ report.quality?.status ?? 'quality unknown' }}</small>
          </RouterLink>
          <article v-if="!reports.length" class="report-card"><span>还没有研究报告。</span></article>
        </div>
        <p v-if="manualReports.length" class="subtle">已保存 {{ manualReports.length }} 份手动即时分析，它们不会参与正式预测评分。</p>
      </section>
    </template>
  </main>
</template>
