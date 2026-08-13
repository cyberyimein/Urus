<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import AppShell from '@/components/AppShell.vue'
import MockBadge from '@/components/MockBadge.vue'
import OptionsPanel from '@/components/OptionsPanel.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import StepTimeline from '@/components/StepTimeline.vue'
import { useUrusStore } from '@/stores/urus'
import type { EventRecord, EventSummary, InstrumentCard, MarketCard, RunType } from '@/types/api'
import { formatDate, formatNumber, nullable, runTypeLabel } from '@/utils/format'

type TabId = 'market' | 'instrument' | 'events' | 'options' | 'decision' | 'quality'

interface DashboardTab {
  id: TabId
  label: string
  meta: string
  status: string
}

interface MacroObservation {
  value?: number
  as_of?: string
  source?: string
  label?: string
  unit?: string
}

const store = useUrusStore()
const route = useRoute()
const activeTab = ref<TabId>(route.query.tab === 'options' ? 'options' : 'market')
const runType = ref<RunType>('pre_market')
const simulateMacroEvent = ref(false)
const simulateInstrumentEvent = ref(false)
const runSteps = computed(() => store.latestRun?.steps ?? [])
const readModel = computed(() => store.latestReadModel)
const decisionData = computed(() => readModel.value?.decision ?? null)
const market = computed<MarketCard | null>(() => readModel.value?.market ?? null)
const instrumentCards = computed<InstrumentCard[]>(() => readModel.value?.instrument_cards ?? [])
const liveInstrumentCount = computed(() => instrumentCards.value.filter((card) => card.is_mock === false).length)
const activeInstrumentTheme = ref('')

function decisionField(key: string): unknown {
  const decision = decisionData.value
  if (!decision || decision.is_mock) return null
  const report = decision.decision_report
  return report && typeof report === 'object' ? (report as Record<string, unknown>)[key] : null
}

function decisionDisplayStance(): string {
  if (!decisionData.value) return '不可用'
  if (decisionData.value.is_mock) return decisionData.value.stance || '不可用'
  const regime = decisionField('market_regime')
  if (regime && typeof regime === 'object') return String((regime as Record<string, unknown>).classification ?? '已生成')
  return decisionData.value.status
}

function decisionDisplayConfidence(): string {
  const decision = decisionData.value
  if (!decision) return '不可用'
  if (decision.is_mock) return decision.confidence === null ? '不可用' : `${(decision.confidence * 100).toFixed(1)}%`
  const regime = decisionField('market_regime')
  if (regime && typeof regime === 'object' && typeof (regime as Record<string, unknown>).confidence === 'number') {
    return `${(Number((regime as Record<string, unknown>).confidence) * 100).toFixed(1)}%`
  }
  return '不可用'
}

function decisionDisplaySummary(): string {
  const decision = decisionData.value
  if (!decision) return '不可用'
  if (decision.is_mock) return decision.summary
  return 'Urus Agent 已完成结构化决策报告；打开报告可查看候选闸门与期权分支。'
}

const instrumentThemesBySymbol: Record<string, string[]> = {
  QQQ: ['ETF'],
  SPY: ['ETF'],
  SMH: ['ETF', '半导体'],
  IGV: ['ETF'],
  INTC: ['半导体'],
  AMD: ['半导体'],
  NVDA: ['半导体'],
  LITE: ['光概念'],
  COHR: ['光概念'],
  MRVL: ['光概念'],
  NOK: ['光概念'],
  MSFT: ['大科技'],
  NOW: ['SaaS'],
  ORCL: ['SaaS'],
  AAPL: ['大科技'],
  AMZN: ['大科技'],
  GOOG: ['大科技'],
  RKLB: ['航天与新兴'],
  NBIS: ['航天与新兴'],
}

const instrumentThemeOrder = ['ETF', '半导体', '光概念', 'SaaS', '大科技', '航天与新兴', '其他关注']

function instrumentThemes(card: InstrumentCard): string[] {
  const payloadThemes = card.themes?.filter((theme) => theme.trim().length > 0)
  if (payloadThemes?.length) return payloadThemes
  if (instrumentThemesBySymbol[card.symbol]) return instrumentThemesBySymbol[card.symbol]
  return [card.theme || (card.asset_type === 'etf' ? 'ETF' : '其他关注')]
}

const instrumentGroups = computed(() => {
  const groups = new Map<string, InstrumentCard[]>()
  for (const card of instrumentCards.value) {
    for (const theme of instrumentThemes(card)) {
      const cards = groups.get(theme) ?? []
      cards.push(card)
      groups.set(theme, cards)
    }
  }
  return [...groups.entries()]
    .sort(([left], [right]) => {
      const leftIndex = instrumentThemeOrder.indexOf(left)
      const rightIndex = instrumentThemeOrder.indexOf(right)
      return (leftIndex < 0 ? instrumentThemeOrder.length : leftIndex)
        - (rightIndex < 0 ? instrumentThemeOrder.length : rightIndex)
    })
    .map(([key, cards]) => ({
      key,
      cards,
      liveCount: cards.filter((card) => card.is_mock === false).length,
    }))
})

const selectedInstrumentGroupKey = computed(() => {
  const groups = instrumentGroups.value
  return groups.some((group) => group.key === activeInstrumentTheme.value)
    ? activeInstrumentTheme.value
    : groups[0]?.key ?? ''
})

const selectedInstrumentGroup = computed(() => (
  instrumentGroups.value.find((group) => group.key === selectedInstrumentGroupKey.value)
  ?? { key: '', cards: [] as InstrumentCard[], liveCount: 0 }
))

const macroCards = [
  { key: 'vix', label: 'VIX', unit: '点' },
  { key: 'us_2y_yield', label: '美国 2Y', unit: '%' },
  { key: 'us_10y_yield', label: '美国 10Y', unit: '%' },
  { key: 'us_30y_yield', label: '美国 30Y', unit: '%' },
  { key: 'us_2s10s_spread', label: '2s10s', unit: '百分点' },
]

const historyReturnCards = [
  { key: '1d', label: '1D' },
  { key: '5d', label: '5D' },
  { key: '20d', label: '20D' },
  { key: '60d', label: '60D' },
  { key: '120d', label: '120D' },
  { key: '252d', label: '252D' },
]

const movingAverageCards = [
  { key: '20d', label: 'MA20' },
  { key: '50d', label: 'MA50' },
  { key: '200d', label: 'MA200' },
]

const tabs = computed<DashboardTab[]>(() => {
  const current = readModel.value
  const snapshot = current?.market?.market_snapshot
  const returnedSnapshotCount = snapshot?.returned_symbols?.length
  const requestedSnapshotCount = snapshot?.requested_symbols?.length
  const instrumentCount = current?.instrument_cards?.length ?? 0
  const liveInstrumentCount = current?.instrument_cards?.filter((card) => card.is_mock === false).length ?? 0
  const eventStatuses = [current?.macro_event.status, current?.instrument_event.status]
  const eventStatus = eventStatuses.includes('failed')
    ? 'failed'
    : eventStatuses.includes('succeeded')
      ? 'succeeded'
      : eventStatuses.includes('skipped')
        ? 'skipped'
        : 'unavailable'
  const isCtaVariant = current?.macro_event.variant === 'cta' || current?.instrument_event.variant === 'cta'

  return [
    {
      id: 'market',
      label: '大盘 / 1A',
      meta:
        returnedSnapshotCount !== undefined && requestedSnapshotCount !== undefined
          ? `${returnedSnapshotCount}/${requestedSnapshotCount} 个快照`
          : '未采集',
      status: current?.market?.quality_status ?? 'unavailable',
    },
    {
      id: 'instrument',
      label: '个股 / 3A',
      meta: instrumentCount > 0 ? `${liveInstrumentCount}/${instrumentCount} live` : '未采集',
      status: current?.instrument?.data_state ?? 'unavailable',
    },
    {
      id: 'events',
      label: isCtaVariant ? 'CTA / 1B + 3B' : '事件 / 1B + 3B',
      meta: isCtaVariant ? '市场 + 跨资产' : '宏观 + 个股',
      status: eventStatus,
    },
    {
      id: 'options',
      label: '期权 / 2',
      meta: current?.options?.available ? '已采集' : '未接入',
      status: current?.options?.data_state ?? 'placeholder',
    },
    {
      id: 'decision',
      label: '决策 / 4',
      meta: current?.decision?.is_mock ? current.decision.stance ?? '未接入' : current?.decision?.status ?? '未接入',
      status: current?.decision?.data_state ?? 'placeholder',
    },
    {
      id: 'quality',
      label: '运行 / 5',
      meta: current?.data_quality.status ?? '未运行',
      status: current?.data_quality.status ?? 'unavailable',
    },
  ]
})

const macroContext = computed(() => market.value?.macro_context ?? null)
const macroObservations = computed(() => macroContext.value?.observations ?? {})
const macroDerived = computed(() => macroContext.value?.derived ?? {})

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : null
}

function toNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value))) return Number(value)
  return null
}

function observation(key: string): MacroObservation | null {
  const value = asRecord(macroObservations.value[key])
  return value as MacroObservation | null
}

function derivedObservation(key: string): MacroObservation | null {
  const value = asRecord(macroDerived.value[key])
  return value as MacroObservation | null
}

function macroValue(key: string): number | null {
  return toNumber(observation(key)?.value ?? derivedObservation(key)?.value)
}

function macroAsOf(key: string): string {
  return observation(key)?.as_of ?? derivedObservation(key)?.as_of ?? '不可用'
}

function macroSource(key: string): string {
  return observation(key)?.source ?? derivedObservation(key)?.source ?? macroContext.value?.source ?? '不可用'
}

function historyValue(section: string, key: string): unknown {
  const history = asRecord(market.value?.history)
  return asRecord(history?.[section])?.[key]
}

function historyTop(key: string): unknown {
  return asRecord(market.value?.history)?.[key]
}

function technicalIndicators(): Record<string, unknown> | null {
  return asRecord(asRecord(market.value?.history)?.technical_indicators)
}

function technicalMetric(key: string): Record<string, unknown> | null {
  return asRecord(technicalIndicators()?.[key])
}

function technicalObject(key: string): Record<string, unknown> | null {
  return technicalMetric(key)
}

function technicalValue(key: string): number | null {
  return toNumber(technicalMetric(key)?.value)
}

function bollingerValue(key: string): number | null {
  return toNumber(technicalMetric('bollinger_20_2')?.[key])
}

function technicalMeta(key: string): string {
  const metric = technicalMetric(key) ?? technicalIndicators()
  if (!metric) return '不可用'
  return `${metric.source || '不可用'} · ${metric.as_of || '不可用'} · n=${metric.sample_count ?? 0}`
}

function technicalSignalValue(section: string, key: string): unknown {
  return technicalObject(section)?.[key] ?? null
}

function instrumentSignalValue(card: InstrumentCard, section: string, key: string): unknown {
  const indicators = card.history?.technical_indicators as Record<string, unknown> | undefined
  const metric = indicators?.[section]
  return asRecord(metric)?.[key] ?? null
}

function signalLabel(value: unknown): string {
  const labels: Record<string, string> = {
    bullish_cross: 'MACD 金叉',
    bearish_cross: 'MACD 死叉',
    none: '无交叉',
    above_zero: '零轴上方',
    below_zero: '零轴下方',
    on_zero: '零轴附近',
    bullish_accelerating: '多头动量增强',
    bullish_fading: '多头动量减弱',
    bearish_accelerating: '空头动量增强',
    bearish_fading: '空头动量减弱',
    flat: '动量平坦',
    volume_down_distribution: '放量下跌 / 派发',
    volume_down_absorption: '放量下跌 / 吸收',
    volume_up_demand: '放量上涨 / 需求',
    volume_up_absorption: '放量上涨 / 吸收',
    low_volume_move: '缩量移动',
    neutral: '中性',
    high: '放量',
    low: '缩量',
    normal: '正常量',
    up: '上涨',
    down: '下跌',
    unavailable: '不可用',
  }
  if (typeof value !== 'string' || value.length === 0) return '不可用'
  return labels[value] ?? value
}

function effortResultLabel(effort: unknown, resultDirection: unknown): string {
  const effortLabels: Record<string, string> = {
    high: '放量',
    normal: '正常量',
    low: '缩量',
  }
  const resultLabels: Record<string, string> = {
    up: '上涨',
    down: '下跌',
    flat: '横盘',
  }
  if (typeof effort !== 'string' || typeof resultDirection !== 'string') return '不可用'
  const effortLabel = effortLabels[effort]
  const resultLabel = resultLabels[resultDirection]
  return effortLabel && resultLabel ? `${effortLabel} / ${resultLabel}` : '不可用'
}

function technicalEffortResultLabel(): string {
  return effortResultLabel(
    technicalSignalValue('volume_effort_result', 'effort'),
    technicalSignalValue('volume_effort_result', 'result_direction'),
  )
}

function instrumentEffortResultLabel(card: InstrumentCard): string {
  return effortResultLabel(
    instrumentSignalValue(card, 'volume_effort_result', 'effort'),
    instrumentSignalValue(card, 'volume_effort_result', 'result_direction'),
  )
}

function signalClass(value: unknown): string {
  if (typeof value !== 'string') return ''
  if (value.startsWith('bullish') || value === 'volume_up_demand' || value === 'volume_up_absorption') return 'positive-text'
  if (value.startsWith('bearish') || value === 'volume_down_distribution' || value === 'volume_down_absorption') return 'negative-text'
  return ''
}

function displayPercent(value: unknown, digits = 2): string {
  const numeric = toNumber(value)
  if (numeric === null) return '不可用'
  return `${numeric >= 0 ? '+' : ''}${numeric.toFixed(digits)}%`
}

function displayVolume(value: unknown): string {
  const numeric = toNumber(value)
  if (numeric === null) return '不可用'
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 }).format(numeric)
}

function displayQuoteTime(value: string | null | undefined): string {
  return value || '不可用'
}

function quoteChangeClass(value: unknown): string {
  const numeric = toNumber(value)
  if (numeric === null) return ''
  return numeric >= 0 ? 'positive-text' : 'negative-text'
}

function instrumentHistoryValue(card: InstrumentCard, key: string): unknown {
  return card.history?.returns_percent?.[key]
}

function instrumentTechnicalValue(card: InstrumentCard, key: string): unknown {
  const indicators = card.history?.technical_indicators as Record<string, unknown> | undefined
  const metric = indicators?.[key]
  return typeof metric === 'object' && metric !== null ? (metric as Record<string, unknown>).value : null
}

function instrumentRelativeValue(card: InstrumentCard, key: string): unknown {
  const relative = card.relative_strength as Record<string, unknown> | undefined
  const values = relative?.excess_returns_percent
  return typeof values === 'object' && values !== null ? (values as Record<string, unknown>)[key] : null
}

function instrumentBollingerValue(card: InstrumentCard, key: string): unknown {
  const indicators = card.history?.technical_indicators as Record<string, unknown> | undefined
  const bollinger = indicators?.bollinger_20_2
  return typeof bollinger === 'object' && bollinger !== null
    ? (bollinger as Record<string, unknown>)[key]
    : null
}

function instrumentTechnicalAsOf(card: InstrumentCard): string {
  const indicators = card.history?.technical_indicators as Record<string, unknown> | undefined
  return typeof indicators?.as_of === 'string' ? indicators.as_of : '不可用'
}

function instrumentHistoryField(card: InstrumentCard, section: string, key: string): unknown {
  const history = card.history as Record<string, unknown> | undefined
  return asRecord(history?.[section])?.[key] ?? null
}

function instrumentLatestBarField(card: InstrumentCard, key: string): unknown {
  const latestBar = instrumentHistoryField(card, 'latest_completed_bar', key)
  return latestBar
}

function instrumentTechnicalMetric(card: InstrumentCard, key: string): Record<string, unknown> | null {
  const indicators = card.history?.technical_indicators as Record<string, unknown> | undefined
  return asRecord(indicators?.[key])
}

function instrumentTechnicalMetricValue(card: InstrumentCard, key: string): number | null {
  return toNumber(instrumentTechnicalMetric(card, key)?.value)
}

function instrumentTechnicalField(card: InstrumentCard, section: string, key: string): unknown {
  return instrumentTechnicalMetric(card, section)?.[key] ?? null
}

function instrumentCloseLocation(card: InstrumentCard): string {
  const ratio = toNumber(instrumentTechnicalField(card, 'volume_effort_result', 'close_location_ratio'))
  return displayPercent(ratio === null ? null : ratio * 100, 1)
}

function instrumentRelativeField(card: InstrumentCard, section: string, key: string): unknown {
  const relative = card.relative_strength as Record<string, unknown> | undefined
  return asRecord(relative?.[section])?.[key] ?? null
}

function allInstrumentHistoryAvailable(): boolean {
  return instrumentCards.value.length > 0 && instrumentCards.value.every((card) => card.history?.available === true)
}

function hasInstrumentRelativeStrength(): boolean {
  return instrumentCards.value.some((card) => card.symbol === 'INTC' && card.relative_strength?.available === true)
}

function instrumentQuotaText(): string {
  const audit = store.latestReadModel?.instrument?.quota_audit as Record<string, unknown> | undefined
  const unchanged = audit?.subscription_unchanged
  const historyDelta = audit?.history_used_delta
  const subscriptionText = unchanged === true ? '订阅状态未变化' : unchanged === false ? '订阅状态有变化' : '订阅额度未返回'
  const historyText = typeof historyDelta === 'number'
    ? `历史 K 线额度 ${historyDelta >= 0 ? '+' : ''}${historyDelta}`
    : '历史 K 线额度未返回'
  return `${subscriptionText} · ${historyText}`
}

function instrumentCardType(card: InstrumentCard): string {
  return card.asset_type === 'etf' || ['QQQ', 'SPY', 'SMH', 'IGV'].includes(card.symbol) ? 'ETF' : '个股'
}

function eventText(event: EventSummary): string {
  return event.summary || event.reason || '当前没有摘要。'
}

function eventResultText(event: EventRecord): string {
  return event.result?.summary || (event.result?.status === 'not_released' ? '尚未发布结果' : '结果尚未采集')
}

async function triggerRun() {
  await store.triggerRun(runType.value, {
    simulateMacroEvent: simulateMacroEvent.value,
    simulateInstrumentEvent: simulateInstrumentEvent.value,
  })
}

onMounted(() => {
  void store.loadDashboard()
})
</script>

<template>
  <AppShell />
  <main class="page-shell validation-page">
    <header class="validation-header">
      <div>
        <p class="eyebrow">STAGE 1A + 2 + 3A / DATA VALIDATION</p>
        <h1>数据采集验证</h1>
      </div>
      <div class="run-launcher compact-launcher">
        <label class="field-label" for="run-type">运行类型</label>
        <select id="run-type" v-model="runType">
          <option value="pre_market">盘前</option>
          <option value="pre_close">收盘前一小时</option>
          <option value="post_close_review">收盘后复盘</option>
        </select>
        <div class="launcher-actions">
          <label class="check-row"><input v-model="simulateMacroEvent" type="checkbox" /><span>模拟宏观事件</span></label>
          <label class="check-row"><input v-model="simulateInstrumentEvent" type="checkbox" /><span>模拟个股事件</span></label>
          <button class="primary-button" :disabled="store.busy" @click="triggerRun">{{ store.busy ? '运行中…' : '开始采集' }}</button>
        </div>
      </div>
    </header>

    <div v-if="store.error" class="error-banner" role="alert">{{ store.error }}</div>

    <section v-if="store.latestRun && store.latestReadModel" class="validation-workspace">
      <div class="connection-strip">
        <div>
          <span class="eyebrow">后端连接</span>
          <strong :data-connection="store.connection">{{ store.connection === 'connected' ? '已连接' : store.connection === 'offline' ? '不可用' : '检查中' }}</strong>
        </div>
        <div class="connection-meta">
          <span>{{ runTypeLabel(store.latestRun.run_type) }}</span>
          <span class="mono">run {{ store.latestRun.id.slice(0, 8) }}</span>
          <span class="mono">snapshot {{ store.latestRun.snapshot_id?.slice(0, 8) || '不可用' }}</span>
        </div>
      </div>

      <div class="run-meta-grid validation-meta">
        <div><span>运行状态</span><strong>{{ store.latestRun.status }}</strong></div>
              <div><span>截止时间（JST）</span><strong>{{ formatDate(store.latestRun.cutoff_time) }}</strong></div>
              <div><span>生成时间（JST）</span><strong>{{ formatDate(store.latestReadModel.generated_at) }}</strong></div>
        <div><span>总体质量</span><strong>{{ store.latestReadModel.data_quality.status }}</strong></div>
      </div>

      <nav class="validation-tabs" aria-label="数据验证模块" role="tablist">
        <button
          v-for="tab in tabs"
          :key="tab.id"
          class="validation-tab"
          :class="{ active: activeTab === tab.id }"
          :aria-selected="activeTab === tab.id"
          role="tab"
          type="button"
          @click="activeTab = tab.id"
        >
          <span class="tab-label">{{ tab.label }}</span>
          <StatusBadge :status="tab.status" />
          <small>{{ tab.meta }}</small>
        </button>
      </nav>

      <section v-if="activeTab === 'market'" class="tab-panel" role="tabpanel">
        <div class="tab-titlebar">
          <div><p class="eyebrow">COLLECTED / 1A</p><h2>大盘数据</h2></div>
          <span v-if="market?.is_mock === false" class="live-badge">Moomoo OpenD</span>
          <MockBadge v-else />
        </div>

        <template v-if="market">
          <section class="data-section">
            <div class="section-label-row"><div><span class="section-kicker">PRIMARY</span><h3>QQQ 当前快照</h3></div><span class="source-label">{{ market.source }}</span></div>
            <div class="metric-grid primary-metrics">
              <div class="metric-cell metric-cell-major"><span>常规现价 / 收盘价</span><strong>{{ formatNumber(market.regular_price ?? market.last_price) }}</strong><small>正规交易价格 · {{ market.session_label || '不可用' }}</small></div>
              <div class="metric-cell"><span>相对昨收</span><strong :class="quoteChangeClass(market.change_percent)">{{ displayPercent(market.change_percent, 4) }}</strong><small>常规：{{ displayPercent(market.regular_change_percent, 4) }}</small></div>
              <div class="metric-cell"><span>昨收</span><strong>{{ formatNumber(market.previous_close) }}</strong><small>报价时间：{{ displayQuoteTime(market.quote_time) }}</small></div>
              <div class="metric-cell"><span>成交量</span><strong>{{ displayVolume(market.volume) }}</strong><small>来源：{{ market.source }}</small></div>
              <div class="metric-cell"><span>盘前</span><strong>{{ formatNumber(market.premarket_price) }}</strong><small>{{ displayVolume(market.premarket_volume) }} · {{ displayPercent(market.premarket_change_percent, 4) }}</small></div>
              <div class="metric-cell"><span>盘后</span><strong>{{ formatNumber(market.afterhours_price) }}</strong><small>{{ displayVolume(market.afterhours_volume) }} · {{ displayPercent(market.afterhours_change_percent, 4) }}</small></div>
            </div>
          </section>

          <section class="data-section">
            <div class="section-label-row"><div><span class="section-kicker">SNAPSHOT UNIVERSE</span><h3>大盘与跨资产代理</h3></div><span class="source-label">{{ market.market_snapshot?.returned_symbols.length ?? 0 }}/{{ market.market_snapshot?.requested_symbols.length ?? 0 }} 返回</span></div>
            <div class="table-wrap">
              <table class="data-table">
                <thead><tr><th>标的</th><th>常规价</th><th>变化</th><th>昨收</th><th>开 / 高 / 低</th><th>成交量</th><th>成交额</th><th>买 / 卖</th><th>价差</th><th>盘前 / 盘后</th><th>报价时间</th></tr></thead>
                <tbody>
                  <tr v-for="quote in market.market_snapshot?.quotes ?? []" :key="quote.quote_code || quote.symbol">
                    <td><strong>{{ quote.symbol }}</strong><small>{{ quote.label }}</small></td>
                    <td>{{ formatNumber(quote.regular_price ?? quote.last_price) }}</td>
                    <td :class="quoteChangeClass(quote.change_percent)">{{ displayPercent(quote.change_percent, 4) }}</td>
                    <td>{{ formatNumber(quote.previous_close) }}</td>
                    <td>{{ formatNumber(quote.open_price) }} / {{ formatNumber(quote.high_price) }} / {{ formatNumber(quote.low_price) }}</td>
                    <td>{{ displayVolume(quote.volume) }}</td>
                    <td>{{ formatNumber(quote.turnover, 0) }}</td>
                    <td>{{ formatNumber(quote.bid_price) }} / {{ formatNumber(quote.ask_price) }}</td>
                    <td>{{ formatNumber(quote.price_spread, 4) }}</td>
                    <td>{{ formatNumber(quote.premarket_price) }} / {{ formatNumber(quote.afterhours_price) }}</td>
                    <td>{{ displayQuoteTime(quote.quote_time) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-if="market.market_snapshot?.unavailable_symbols.length" class="notice-box warning-box"><strong>未返回标的</strong><span>{{ market.market_snapshot.unavailable_symbols.join('、') }}</span></div>
          </section>

          <section class="data-section">
            <div class="section-label-row"><div><span class="section-kicker">MACRO CONTEXT</span><h3>宏观日频数据</h3></div><span class="source-label">{{ macroContext?.source || '不可用' }} · {{ macroContext?.quality_status || '不可用' }}</span></div>
            <div class="metric-grid macro-metrics">
              <div v-for="item in macroCards" :key="item.key" class="metric-cell"><span>{{ item.label }}</span><strong>{{ formatNumber(macroValue(item.key)) }} {{ item.unit }}</strong><small>{{ macroSource(item.key) }} · {{ macroAsOf(item.key) }}</small></div>
            </div>
            <div v-if="market.market_snapshot?.vix && !market.market_snapshot.vix.available" class="notice-box warning-box"><strong>Moomoo 直接 VIX（已跳过）</strong><span>{{ market.market_snapshot.vix.reason || '按策略不请求美国指数' }}。上方 VIX 按 Yahoo/FRED 宏观日频源显示。</span></div>
            <div v-if="macroContext?.quality_warnings.length" class="notice-box"><strong>宏观数据提示</strong><span>{{ macroContext.quality_warnings.join('；') }}</span></div>
          </section>

          <section class="data-section">
            <div class="section-label-row"><div><span class="section-kicker">HISTORY SUMMARY</span><h3>QQQ 日线摘要</h3></div><span class="source-label">{{ historyTop('returned_days') || 0 }} / {{ historyTop('requested_days') || 0 }} 根返回</span></div>
            <div class="metric-grid history-metrics">
              <div v-for="item in historyReturnCards" :key="item.key" class="metric-cell"><span>{{ item.label }} 收益</span><strong>{{ displayPercent(historyValue('returns_percent', item.key), 2) }}</strong><small>当前仅有已返回窗口</small></div>
              <div v-for="item in movingAverageCards" :key="item.key" class="metric-cell"><span>{{ item.label }}</span><strong>{{ formatNumber(toNumber(historyValue('moving_average', item.key))) }}</strong><small>复权日线摘要</small></div>
            </div>
          </section>

          <section class="data-section">
            <div class="section-label-row"><div><span class="section-kicker">TECHNICAL INDICATORS</span><h3>QQQ 日线波动与通道</h3></div><span class="source-label">{{ technicalIndicators()?.quality_status || '不可用' }}</span></div>
            <div class="metric-grid history-metrics">
              <div class="metric-cell"><span>20D 年化实现波动率</span><strong>{{ displayPercent(technicalValue('realized_volatility_20d'), 2) }}</strong><small>{{ technicalMeta('realized_volatility_20d') }}</small></div>
              <div class="metric-cell"><span>ATR14</span><strong>{{ formatNumber(technicalValue('atr14'), 4) }}</strong><small>{{ technicalMeta('atr14') }} · 绝对值</small></div>
              <div class="metric-cell"><span>ATR14%</span><strong>{{ displayPercent(technicalValue('atr14_percent'), 2) }}</strong><small>{{ technicalMeta('atr14_percent') }}</small></div>
              <div class="metric-cell"><span>布林上 / 下轨 20/1σ</span><strong>{{ formatNumber(toNumber(technicalMetric('bollinger_20_1')?.upper), 4) }} / {{ formatNumber(toNumber(technicalMetric('bollinger_20_1')?.lower), 4) }}</strong><small>{{ technicalMeta('bollinger_20_1') }}</small></div>
              <div class="metric-cell"><span>布林上轨 20/2</span><strong>{{ formatNumber(bollingerValue('upper'), 4) }}</strong><small>{{ technicalMeta('bollinger_20_2') }}</small></div>
              <div class="metric-cell"><span>布林中轨 20/2</span><strong>{{ formatNumber(bollingerValue('middle'), 4) }}</strong><small>{{ technicalMeta('bollinger_20_2') }}</small></div>
              <div class="metric-cell"><span>布林下轨 20/2</span><strong>{{ formatNumber(bollingerValue('lower'), 4) }}</strong><small>{{ technicalMeta('bollinger_20_2') }}</small></div>
              <div class="metric-cell"><span>布林上 / 下轨 20/3σ</span><strong>{{ formatNumber(toNumber(technicalMetric('bollinger_20_3')?.upper), 4) }} / {{ formatNumber(toNumber(technicalMetric('bollinger_20_3')?.lower), 4) }}</strong><small>{{ technicalMeta('bollinger_20_3') }}</small></div>
              <div class="metric-cell"><span>布林带宽 20/2σ</span><strong>{{ displayPercent(technicalValue('bollinger_bandwidth_20'), 2) }}</strong><small>{{ technicalMeta('bollinger_bandwidth_20') }}</small></div>
              <div class="metric-cell"><span>布林当前位置</span><strong>{{ displayPercent(bollingerValue('position_percent'), 2) }}</strong><small>当前价 {{ formatNumber(bollingerValue('current_price'), 4) }} · {{ technicalMeta('bollinger_20_2') }}</small></div>
              <div class="metric-cell"><span>MACD DIF / DEA</span><strong>{{ formatNumber(toNumber(technicalSignalValue('macd_12_26_9', 'dif')), 4) }} / {{ formatNumber(toNumber(technicalSignalValue('macd_12_26_9', 'dea')), 4) }}</strong><small>{{ technicalMeta('macd_12_26_9') }}</small></div>
              <div class="metric-cell"><span>MACD 柱体</span><strong>{{ formatNumber(toNumber(technicalSignalValue('macd_12_26_9', 'histogram')), 4) }}</strong><small :class="signalClass(technicalSignalValue('macd_12_26_9', 'momentum'))">{{ signalLabel(technicalSignalValue('macd_12_26_9', 'momentum')) }} · {{ signalLabel(technicalSignalValue('macd_12_26_9', 'zero_axis')) }}</small></div>
              <div class="metric-cell"><span>MACD 交叉</span><strong :class="signalClass(technicalSignalValue('macd_12_26_9', 'crossover'))">{{ signalLabel(technicalSignalValue('macd_12_26_9', 'crossover')) }}</strong><small>参数 12 / 26 / 9 · {{ technicalMeta('macd_12_26_9') }}</small></div>
              <div class="metric-cell"><span>Effort vs Result</span><strong :class="signalClass(technicalSignalValue('volume_effort_result', 'signal'))">{{ signalLabel(technicalSignalValue('volume_effort_result', 'signal')) }}</strong><small>{{ technicalEffortResultLabel() }} · {{ displayPercent(technicalSignalValue('volume_effort_result', 'volume_ratio_20d'), 2) }} 量比</small></div>
            </div>
          </section>

          <section class="data-section unfinished-section">
            <div class="section-label-row"><div><span class="section-kicker">NOT COLLECTED</span><h3>当前未接入</h3></div></div>
            <div class="unfinished-list"><span>5年日线原始归档</span><span>市场涨跌家数</span><span>行业热力图</span><span>5分钟 OHLCV</span><span>交易日历与提前收盘（自动调度前补）</span><span>个股财务与事件（3B）</span></div>
          </section>
        </template>
        <div v-else class="empty-panel"><h3>大盘数据不可用</h3><p>1A 步骤没有返回市场数据。</p></div>
      </section>

      <section v-else-if="activeTab === 'instrument'" class="tab-panel" role="tabpanel">
        <div class="tab-titlebar"><div><p class="eyebrow">COLLECTED / 3A</p><h2>个股与行业 ETF</h2></div><StatusBadge :status="store.latestReadModel.instrument?.data_state ?? 'unavailable'" /></div>
        <template v-if="instrumentCards.length">
          <nav class="instrument-theme-tabs" aria-label="3A 主题分组">
            <button
              v-for="group in instrumentGroups"
              :key="group.key"
              type="button"
              :class="{ active: selectedInstrumentGroupKey === group.key }"
              @click="activeInstrumentTheme = group.key"
            >
              <strong>{{ group.key }}</strong>
              <small>{{ group.liveCount }}/{{ group.cards.length }} live</small>
            </button>
          </nav>
          <section class="data-section">
            <div class="section-label-row"><div><span class="section-kicker">CURRENT UNIVERSE · {{ selectedInstrumentGroup.key }}</span><h3>{{ selectedInstrumentGroup.cards.length }} 个标的摘要</h3></div><span class="source-label">{{ liveInstrumentCount }}/{{ instrumentCards.length }} live · 本轮 {{ formatDate(store.latestReadModel.generated_at) }}</span></div>
            <div class="table-wrap">
              <table class="data-table instrument-summary-table">
                <thead><tr><th>标的</th><th>类型</th><th>常规价</th><th>盘前 / 盘后</th><th>日变化</th><th>5D / 20D</th><th>相对 QQQ 20D</th><th>布林 %B</th><th>MACD / 量价</th><th>日线截至</th><th>状态</th></tr></thead>
                <tbody>
                  <tr v-for="card in selectedInstrumentGroup.cards" :key="card.symbol">
                    <td><strong>{{ card.symbol }}</strong><small>{{ card.label }}</small></td>
                    <td>{{ instrumentCardType(card) }}</td>
                    <td>{{ formatNumber(card.regular_price ?? card.last_price) }}</td>
                    <td>{{ formatNumber(card.premarket_price) }} / {{ formatNumber(card.afterhours_price) }}</td>
                    <td :class="quoteChangeClass(card.change_percent)">{{ displayPercent(card.change_percent, 4) }}</td>
                    <td>{{ displayPercent(instrumentHistoryValue(card, '5d'), 2) }} / {{ displayPercent(instrumentHistoryValue(card, '20d'), 2) }}</td>
                    <td>{{ displayPercent(instrumentRelativeValue(card, '20d'), 2) }}</td>
                    <td>{{ displayPercent(instrumentBollingerValue(card, 'position_percent'), 2) }}</td>
                    <td><strong :class="signalClass(instrumentSignalValue(card, 'macd_12_26_9', 'momentum'))">{{ signalLabel(instrumentSignalValue(card, 'macd_12_26_9', 'momentum')) }}</strong><small>{{ signalLabel(instrumentSignalValue(card, 'volume_effort_result', 'signal')) }} · {{ instrumentEffortResultLabel(card) }}</small></td>
                    <td>{{ instrumentTechnicalAsOf(card) }}</td>
                    <td><StatusBadge :status="card.data_state ?? 'unavailable'" /></td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
          <section class="data-section">
            <div class="section-label-row"><div><span class="section-kicker">FULL TECHNICAL READOUT · {{ selectedInstrumentGroup.key }}</span><h3>完整字段详情</h3></div><span class="source-label">点击标的展开 · 可重算日线技术指标</span></div>
            <div class="instrument-detail-list">
              <details v-for="card in selectedInstrumentGroup.cards" :key="`${card.symbol}-detail`" class="instrument-detail">
                <summary><span><strong>{{ card.symbol }}</strong><small>{{ instrumentThemes(card).join(' · ') }} · {{ instrumentCardType(card) }} · {{ card.provider || card.source || '不可用' }}</small></span><span>{{ signalLabel(instrumentSignalValue(card, 'macd_12_26_9', 'momentum')) }} · 日线 {{ instrumentTechnicalAsOf(card) }}</span></summary>
                <div class="instrument-detail-body">
                  <div class="metric-grid instrument-detail-grid">
                    <div class="metric-cell"><span>最新完成 K 线</span><strong>{{ instrumentLatestBarField(card, 'date') }}</strong><small>O {{ formatNumber(toNumber(instrumentLatestBarField(card, 'open')), 2) }} · H {{ formatNumber(toNumber(instrumentLatestBarField(card, 'high')), 2) }} · L {{ formatNumber(toNumber(instrumentLatestBarField(card, 'low')), 2) }} · C {{ formatNumber(toNumber(instrumentLatestBarField(card, 'close')), 2) }}</small></div>
                    <div class="metric-cell"><span>成交量 / 成交额</span><strong>{{ displayVolume(instrumentLatestBarField(card, 'volume')) }}</strong><small>成交额 {{ formatNumber(toNumber(instrumentLatestBarField(card, 'turnover')), 0) }} · 换手 {{ displayPercent(instrumentLatestBarField(card, 'turnover_rate'), 2) }}</small></div>
                    <div class="metric-cell"><span>绝对收益 1D / 5D / 20D</span><strong>{{ displayPercent(instrumentHistoryValue(card, '1d'), 2) }} / {{ displayPercent(instrumentHistoryValue(card, '5d'), 2) }} / {{ displayPercent(instrumentHistoryValue(card, '20d'), 2) }}</strong><small>60D {{ displayPercent(instrumentHistoryValue(card, '60d'), 2) }} · 120D {{ displayPercent(instrumentHistoryValue(card, '120d'), 2) }} · 252D {{ displayPercent(instrumentHistoryValue(card, '252d'), 2) }}</small></div>
                    <div class="metric-cell"><span>相对 QQQ 5D / 20D / 60D</span><strong>{{ displayPercent(instrumentRelativeField(card, 'excess_returns_percent', '5d'), 2) }} / {{ displayPercent(instrumentRelativeField(card, 'excess_returns_percent', '20d'), 2) }} / {{ displayPercent(instrumentRelativeField(card, 'excess_returns_percent', '60d'), 2) }}</strong><small>Beta20/60 {{ formatNumber(toNumber(instrumentRelativeField(card, 'beta', '20d')), 2) }} / {{ formatNumber(toNumber(instrumentRelativeField(card, 'beta', '60d')), 2) }} · 相关性 {{ formatNumber(toNumber(instrumentRelativeField(card, 'correlation', '20d')), 2) }} / {{ formatNumber(toNumber(instrumentRelativeField(card, 'correlation', '60d')), 2) }}</small></div>
                    <div class="metric-cell"><span>MA10 / MA20 / MA50</span><strong>{{ formatNumber(toNumber(instrumentHistoryField(card, 'moving_average', '10d')), 2) }} / {{ formatNumber(toNumber(instrumentHistoryField(card, 'moving_average', '20d')), 2) }} / {{ formatNumber(toNumber(instrumentHistoryField(card, 'moving_average', '50d')), 2) }}</strong><small>MA100 {{ formatNumber(toNumber(instrumentHistoryField(card, 'moving_average', '100d')), 2) }} · MA200 {{ formatNumber(toNumber(instrumentHistoryField(card, 'moving_average', '200d')), 2) }}</small></div>
                    <div class="metric-cell"><span>实现波动率 10D / 20D / 60D</span><strong>{{ displayPercent(instrumentTechnicalMetricValue(card, 'realized_volatility_10d'), 2) }} / {{ displayPercent(instrumentTechnicalMetricValue(card, 'realized_volatility_20d'), 2) }} / {{ displayPercent(instrumentTechnicalMetricValue(card, 'realized_volatility_60d'), 2) }}</strong><small>ATR14 {{ formatNumber(instrumentTechnicalMetricValue(card, 'atr14'), 4) }} · ATR14% {{ displayPercent(instrumentTechnicalMetricValue(card, 'atr14_percent'), 2) }}</small></div>
                    <div class="metric-cell"><span>布林 20/1σ 上 / 中 / 下</span><strong>{{ formatNumber(toNumber(instrumentTechnicalField(card, 'bollinger_20_1', 'upper')), 2) }} / {{ formatNumber(toNumber(instrumentTechnicalField(card, 'bollinger_20_1', 'middle')), 2) }} / {{ formatNumber(toNumber(instrumentTechnicalField(card, 'bollinger_20_1', 'lower')), 2) }}</strong><small>%B {{ displayPercent(instrumentTechnicalField(card, 'bollinger_20_1', 'position_percent'), 2) }}</small></div>
                    <div class="metric-cell"><span>布林 20/2σ 上 / 中 / 下</span><strong>{{ formatNumber(toNumber(instrumentTechnicalField(card, 'bollinger_20_2', 'upper')), 2) }} / {{ formatNumber(toNumber(instrumentTechnicalField(card, 'bollinger_20_2', 'middle')), 2) }} / {{ formatNumber(toNumber(instrumentTechnicalField(card, 'bollinger_20_2', 'lower')), 2) }}</strong><small>%B {{ displayPercent(instrumentTechnicalField(card, 'bollinger_20_2', 'position_percent'), 2) }} · 带宽 {{ displayPercent(instrumentTechnicalMetricValue(card, 'bollinger_bandwidth_20'), 2) }}</small></div>
                    <div class="metric-cell"><span>布林 20/3σ 上 / 中 / 下</span><strong>{{ formatNumber(toNumber(instrumentTechnicalField(card, 'bollinger_20_3', 'upper')), 2) }} / {{ formatNumber(toNumber(instrumentTechnicalField(card, 'bollinger_20_3', 'middle')), 2) }} / {{ formatNumber(toNumber(instrumentTechnicalField(card, 'bollinger_20_3', 'lower')), 2) }}</strong><small>%B {{ displayPercent(instrumentTechnicalField(card, 'bollinger_20_3', 'position_percent'), 2) }}</small></div>
                    <div class="metric-cell"><span>MACD DIF / DEA / 柱体</span><strong>{{ formatNumber(toNumber(instrumentTechnicalField(card, 'macd_12_26_9', 'dif')), 4) }} / {{ formatNumber(toNumber(instrumentTechnicalField(card, 'macd_12_26_9', 'dea')), 4) }} / {{ formatNumber(toNumber(instrumentTechnicalField(card, 'macd_12_26_9', 'histogram')), 4) }}</strong><small>{{ signalLabel(instrumentTechnicalField(card, 'macd_12_26_9', 'crossover')) }} · {{ signalLabel(instrumentTechnicalField(card, 'macd_12_26_9', 'zero_axis')) }}</small></div>
                    <div class="metric-cell"><span>MACD 动量</span><strong :class="signalClass(instrumentSignalValue(card, 'macd_12_26_9', 'momentum'))">{{ signalLabel(instrumentSignalValue(card, 'macd_12_26_9', 'momentum')) }}</strong><small>前一柱 {{ formatNumber(toNumber(instrumentTechnicalField(card, 'macd_12_26_9', 'previous_histogram')), 4) }} · 参数 12 / 26 / 9</small></div>
                    <div class="metric-cell"><span>Effort vs Result</span><strong :class="signalClass(instrumentSignalValue(card, 'volume_effort_result', 'signal'))">{{ signalLabel(instrumentSignalValue(card, 'volume_effort_result', 'signal')) }}</strong><small>{{ instrumentEffortResultLabel(card) }} · {{ instrumentTechnicalField(card, 'volume_effort_result', 'signal_strength') || '不可用' }}</small></div>
                    <div class="metric-cell"><span>量比 / 20D 均量</span><strong>{{ formatNumber(toNumber(instrumentTechnicalField(card, 'volume_effort_result', 'volume_ratio_20d')), 2) }}x</strong><small>{{ displayVolume(instrumentTechnicalField(card, 'volume_effort_result', 'latest_volume')) }} / {{ displayVolume(instrumentTechnicalField(card, 'volume_effort_result', 'volume_sma_20')) }}</small></div>
                    <div class="metric-cell"><span>单日结果 / 波幅</span><strong>{{ displayPercent(instrumentTechnicalField(card, 'volume_effort_result', 'return_1d_percent'), 2) }}</strong><small>TR {{ formatNumber(toNumber(instrumentTechnicalField(card, 'volume_effort_result', 'true_range')), 2) }} · TR/ATR {{ formatNumber(toNumber(instrumentTechnicalField(card, 'volume_effort_result', 'range_atr_ratio')), 2) }}</small></div>
                    <div class="metric-cell"><span>收盘位置</span><strong>{{ instrumentCloseLocation(card) }}</strong><small>信号阈值由 Effort vs Result payload 记录</small></div>
                  </div>
                </div>
              </details>
            </div>
          </section>
          <section class="data-section"><div class="section-label-row"><div><span class="section-kicker">FIELD STATUS</span><h3>技术验证状态</h3></div></div><div class="status-list"><div><span>行情快照</span><StatusBadge :status="store.latestReadModel.instrument?.data_state ?? 'unavailable'" /><small>{{ store.latestReadModel.instrument?.provider || store.latestReadModel.instrument?.source || '不可用' }} · {{ store.latestReadModel.instrument?.source_mode || '不可用' }} · {{ formatDate(store.latestReadModel.instrument?.captured_at || null) }}</small></div><div><span>历史日线</span><StatusBadge :status="allInstrumentHistoryAvailable() ? 'succeeded' : 'partial'" /><small>来源：Moomoo OpenD；{{ instrumentCards.length }} 个标的均按当前主题分组展示，指标可从保存的原始 K 线重算</small></div><div><span>相对强弱</span><StatusBadge :status="hasInstrumentRelativeStrength() ? 'succeeded' : 'unavailable'" /><small>全部非 QQQ 标的相对 QQQ；财务与事件属于后续阶段</small></div><div><span>额度审计</span><StatusBadge :status="store.latestReadModel.instrument?.quota_audit?.subscription_unchanged === true ? 'succeeded' : 'partial'" /><small>{{ instrumentQuotaText() }}</small></div></div></section>
        </template>
        <div v-else class="empty-panel"><h3>个股数据不可用</h3><p>3A 步骤没有返回个股数据。</p></div>
      </section>

      <section v-else-if="activeTab === 'events'" class="tab-panel" role="tabpanel">
        <div class="tab-titlebar"><div><p class="eyebrow">RESEARCH OVERLAY / 1B + 3B</p><h2>{{ store.latestReadModel.macro_event.variant === 'cta' ? 'CTA 与系统化资金压力' : '事件数据' }}</h2></div><span class="source-label">{{ store.latestReadModel.macro_event.variant === 'cta' ? '确定性 ETF 代理模型' : '只在命中条件时运行' }}</span></div>
        <div class="event-tab-grid">
          <section v-for="event in [store.latestReadModel.macro_event, store.latestReadModel.instrument_event]" :key="event.category" class="event-panel">
            <div class="section-label-row"><div><span class="section-kicker">{{ event.category === 'macro' ? '1B' : '3B' }}</span><h3>{{ event.variant === 'cta' ? (event.category === 'macro' ? '市场 CTA 压力' : '跨资产代理压力') : (event.category === 'macro' ? '宏观事件' : '个股事件') }}</h3></div><StatusBadge :status="event.status" /></div>
            <template v-if="event.variant === 'cta'">
              <dl class="field-list">
                <div><dt>有效信号</dt><dd>{{ event.aggregate?.signal_count || 0 }} / {{ event.expected_symbols?.length || 0 }}</dd></div>
                <div><dt>平均目标暴露</dt><dd>{{ formatNumber(event.aggregate?.average_target_exposure ?? null, 3) }}</dd></div>
                <div><dt>平均压力</dt><dd>{{ formatNumber(event.aggregate?.average_pressure_index ?? null, 1) }}</dd></div>
                <div><dt>方向 / 边际变化</dt><dd>{{ event.aggregate?.classification || 'unavailable' }} · {{ event.aggregate?.pressure_classification || 'unavailable' }}</dd></div>
                <div><dt>摘要</dt><dd>{{ event.summary || '暂无 CTA 代理结果' }}</dd></div>
              </dl>
              <div v-if="event.signals?.length" class="event-record-list">
                <article v-for="signal in event.signals" :key="signal.symbol" class="event-record">
                  <div class="event-record-header"><strong>{{ signal.symbol }} → {{ signal.proxy_for }}</strong><StatusBadge :status="signal.quality_status" /></div>
                  <small>{{ signal.as_of || '无日期' }} · {{ signal.sample_count }} 根日线 · {{ signal.source_mode }}</small>
                  <p v-if="signal.available">目标暴露 {{ formatNumber(signal.target_exposure ?? null, 3) }} · 边际变化 {{ formatNumber(signal.exposure_change ?? null, 3) }} · 压力 {{ formatNumber(signal.pressure_index ?? null, 1) }} · {{ signal.direction }} / {{ signal.pressure_direction }}</p>
                  <p v-else>{{ signal.warnings.join('；') || '数据不足' }}</p>
                </article>
              </div>
              <p v-else class="empty-state compact">当前没有可计算的 CTA 代理日线。</p>
              <div v-if="event.warnings?.length" class="notice-box warning-box"><strong>模型边界</strong><span>{{ event.warnings.join('；') }}</span></div>
            </template>
            <template v-else>
            <dl class="field-list">
              <div><dt>状态</dt><dd>{{ event.status }}</dd></div>
              <div><dt>模式 / Agent</dt><dd>{{ event.mode || 'scheduled' }} · {{ event.agent || '未配置' }}</dd></div>
              <div><dt>未来日历步骤</dt><dd>{{ event.reason === 'expected_events_enabled=false' ? '未启用' : `${event.schedule_step?.status || 'skipped'} · ${event.schedule_step?.api_called ? '已调用日历 API' : '未调用 API'}` }}</dd></div>
              <div><dt>历史结果步骤</dt><dd>{{ event.reason === 'expected_events_enabled=false' ? '未启用' : `${event.result_step?.status || 'skipped'} · 调用结果 API ${event.result_step?.api_call_count || 0} 次` }}</dd></div>
              <div v-if="event.missing_future_definitions?.length"><dt>待补未来定义</dt><dd>{{ event.missing_future_definitions.join('、') }}</dd></div>
              <div><dt>摘要</dt><dd>{{ eventText(event) }}</dd></div>
              <div><dt>下次检查</dt><dd>{{ formatDate(event.next_check_at || null) }}</dd></div>
              <div><dt>复盘反应</dt><dd>{{ event.market_reaction_count || 0 }} 条</dd></div>
            </dl>
            <div v-if="event.warnings?.length" class="notice-box warning-box"><strong>调查提示</strong><span>{{ event.warnings.join('；') }}</span></div>
            <div v-if="event.events?.length" class="event-record-list">
              <article v-for="record in event.events" :key="record.event_key" class="event-record">
                <div class="event-record-header"><strong>{{ record.title }}</strong><StatusBadge :status="record.status" /></div>
                <small>{{ record.subject }} · 计划 {{ formatDate(record.scheduled_at) }} · 结果检查 {{ formatDate(record.result_expected_at) }}</small>
                <p>{{ eventResultText(record) }}</p>
                <div v-if="record.sources.length" class="event-sources">来源：<a v-for="source in record.sources" :key="source.url" :href="source.url" target="_blank" rel="noreferrer">{{ source.publisher }}</a></div>
              </article>
            </div>
            <p v-else class="empty-state compact">当前没有登记的预期事件。</p>
            </template>
          </section>
        </div>
      </section>

      <OptionsPanel v-else-if="activeTab === 'options'" :options="store.latestReadModel.options" />

      <section v-else-if="activeTab === 'decision'" class="tab-panel" role="tabpanel">
        <div class="tab-titlebar"><div><p class="eyebrow">URUS AGENT / 4</p><h2>决策输出</h2></div><StatusBadge :status="store.latestReadModel.decision.data_state" /></div>
        <section class="data-section"><div class="metric-grid primary-metrics"><div class="metric-cell"><span>状态</span><strong>{{ store.latestReadModel.decision.status }}</strong><small>{{ store.latestReadModel.decision.is_mock ? '当前为占位结果' : '结构化 AI 输出' }}</small></div><div class="metric-cell"><span>市场姿态</span><strong>{{ decisionDisplayStance() }}</strong><small>{{ store.latestReadModel.decision.is_mock ? '未调用决策 AI' : '来自 market_regime' }}</small></div><div class="metric-cell"><span>置信度</span><strong>{{ decisionDisplayConfidence() }}</strong><small>{{ store.latestReadModel.decision.is_mock ? '未计算' : '来自 market_regime' }}</small></div></div><div class="notice-box"><strong>开发期结果</strong><span>{{ decisionDisplaySummary() }}</span><span>{{ store.latestReadModel.decision.note }}</span><RouterLink v-if="store.latestRun" class="secondary-button decision-report-link" :to="`/runs/${store.latestRun.id}/report`">打开研究报告 →</RouterLink></div></section>
      </section>

      <section v-else class="tab-panel" role="tabpanel">
        <div class="tab-titlebar"><div><p class="eyebrow">RUN / 5</p><h2>运行与数据质量</h2></div><StatusBadge :status="store.latestReadModel.data_quality.status" /></div>
        <section class="data-section"><div class="section-label-row"><div><span class="section-kicker">QUALITY SUMMARY</span><h3>{{ store.latestReadModel.data_quality.message }}</h3></div></div><div class="quality-summary"><div><span>状态</span><strong>{{ store.latestReadModel.data_quality.status }}</strong></div><div><span>schema</span><strong>{{ store.latestReadModel.schema_version }}</strong></div><div><span>data mode</span><strong>{{ store.latestReadModel.data_mode }}</strong></div><div><span>snapshot</span><strong class="mono">{{ store.latestReadModel.snapshot_id }}</strong></div></div></section>
        <section class="data-section"><div class="section-label-row"><div><span class="section-kicker">WARNINGS</span><h3>提示与错误</h3></div></div><div v-if="store.latestReadModel.data_quality.warnings.length" class="notice-list"><p v-for="warning in store.latestReadModel.data_quality.warnings" :key="warning" class="notice-box warning-box">{{ warning }}</p></div><p v-else class="empty-state compact">没有质量提示。</p><div v-if="store.latestReadModel.data_quality.errors.length" class="notice-list"><p v-for="error in store.latestReadModel.data_quality.errors" :key="error" class="notice-box danger-box">{{ error }}</p></div></section>
        <section class="data-section"><div class="section-label-row"><div><span class="section-kicker">WORKFLOW</span><h3>步骤状态</h3></div></div><StepTimeline :steps="runSteps" /></section>
        <details class="raw-preview"><summary>查看当前 read model JSON</summary><pre>{{ JSON.stringify(store.latestReadModel, null, 2) }}</pre></details>
      </section>
    </section>

    <section v-else-if="!store.error" class="empty-panel"><p class="eyebrow">NO RUN</p><h2>还没有采集结果。</h2><p>开始一次运行后，这里会按 Tab 展示每个数据模块。</p></section>
  </main>
</template>
