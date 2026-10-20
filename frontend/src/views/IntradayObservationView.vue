<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import { getIntradayOverview } from '@/api/intraday'
import AppShell from '@/components/AppShell.vue'
import IntradayEventPanel from '@/components/IntradayEventPanel.vue'
import SystematicRiskCard from '@/components/SystematicRiskCard.vue'
import type {
  IntradayMacdSeries,
  IntradayProjection,
  IntradaySymbolProjection,
} from '@/types/intraday'
import type { IntradayFrozenLevel } from '@/types/intradayEvents'
import '@/styles/intraday.css'

type RoleFilter = 'all' | 'A' | 'B' | 'C' | 'D'

const overview = ref<IntradayProjection | null>(null)
const selectedSymbol = ref('')
const frozenLevels = ref<IntradayFrozenLevel[]>([])
const roleFilter = ref<RoleFilter>('all')
const poolQuery = ref('')
const expandedPoolGroups = ref<Record<string, boolean>>({})
const loading = ref(true)
const error = ref('')
const refreshToken = ref(0)
const REFRESH_INTERVAL_MS = 5 * 60_000
let overviewController: AbortController | null = null
let overviewGeneration = 0
let refreshTimer: number | undefined

const symbolRows = computed(() => Object.values(overview.value?.symbols ?? {}).sort((left, right) => {
  const roleOrder: Record<string, number> = { A: 0, B: 1, C: 2, D: 3 }
  return (roleOrder[left.role ?? ''] ?? 4) - (roleOrder[right.role ?? ''] ?? 4)
    || left.symbol.localeCompare(right.symbol)
}))

const filteredSymbols = computed(() => {
  const query = poolQuery.value.trim().toLocaleUpperCase()
  return symbolRows.value.filter((item) => (
    (roleFilter.value === 'all' || item.role === roleFilter.value)
    && (!query || `${item.symbol} ${poolGroupName(item)}`.toLocaleUpperCase().includes(query))
  ))
})
const poolGroups = computed(() => {
  const groups = new Map<string, IntradaySymbolProjection[]>()
  for (const item of filteredSymbols.value) {
    const name = poolGroupName(item)
    const members = groups.get(name) ?? []
    members.push(item)
    groups.set(name, members)
  }
  return [...groups].map(([name, members]) => ({ name, members }))
})

const selectedSummary = computed(() => overview.value?.symbols[selectedSymbol.value] ?? null)
const currentStatus = computed(() => selectedSummary.value?.status ?? '')
const features = computed(() => selectedSummary.value?.features ?? null)
const selectedQuote = computed(() => selectedSummary.value?.quote ?? null)
const bars = computed(() => selectedSummary.value?.bars ?? [])
const reasons = computed(() => selectedSummary.value?.reasons ?? {})
const macd = computed(() => features.value?.macd ?? null)

const statusLabels: Record<string, string> = {
  disabled: '功能已关闭',
  no_data: '尚无已采集数据',
  invalid_source: '数据状态异常',
  fresh: '数据及时',
  partial: '覆盖不完整',
  stale: '数据陈旧',
  clock_skew: '时钟顺序异常',
  ok: '完整',
  quote_only: '本轮仅报价',
  unavailable: '分钟线不可用',
  not_found: '标的未在最新数据中',
}

const roleLabels: Record<string, string> = {
  A: '大盘 ETF',
  B: '板块 ETF',
  C: '重点股',
  D: '轮换股',
}

function statusLabel(value: string | null | undefined): string {
  return value ? statusLabels[value] ?? value : '未知'
}

function poolGroupName(item: IntradaySymbolProjection): string {
  return item.observation_group?.trim() || `采集角色 · ${roleLabels[item.role ?? ''] ?? '未分组'}`
}

function isPoolGroupOpen(group: { name: string; members: IntradaySymbolProjection[] }): boolean {
  return expandedPoolGroups.value[group.name]
    ?? group.members.some((item) => item.symbol === selectedSymbol.value)
}

function togglePoolGroup(group: { name: string; members: IntradaySymbolProjection[] }) {
  expandedPoolGroups.value[group.name] = !isPoolGroupOpen(group)
}

function formatPrice(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : '—'
}

function formatNumber(value: unknown, digits = 3): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—'
}

function formatPercent(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
    : '—'
}

function formatRatio(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${(value * 100).toFixed(0)}%`
    : '—'
}

function formatEastern(value: unknown): string {
  if (typeof value !== 'string' || !value) return '—'
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'America/New_York',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(date)
}

function formatEasternTime(value: string): string {
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return '—'
  return new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(date)
}

function metricReason(key: string): string {
  return reasons.value[key] ?? ''
}

function displayWarning(warning: string): string {
  if (warning.includes('provider 分钟线时间边界尚未验证')) {
    return '分钟线时间标记尚未确认，当前按采集记录中的开始与结束时间展示。'
  }
  return warning
    .replaceAll('sidecar', '采集数据')
    .replaceAll('provider', '行情来源')
    .replaceAll('capture', '采集记录')
}

function findMacdSeries(seriesId: string): IntradayMacdSeries | null {
  return macd.value?.series?.find((item) => item.series_id === seriesId) ?? null
}

const macdMetrics = [
  {
    key: 'histogram',
    seriesId: 'macd_histogram',
    label: 'H · 柱状图状态',
    formula: 'H = DIF − DEA',
    meaning: '正：快线高于信号线 · 负：快线低于信号线',
    unit: '价格',
  },
  {
    key: 'histogram_delta',
    seriesId: 'macd_histogram_delta',
    label: 'ΔH · 斜率',
    formula: 'ΔH = 本根 H − 上根 H',
    meaning: '正：柱值上升（含负柱收窄） · 负：柱值下降',
    unit: '价格 / 根',
  },
  {
    key: 'histogram_delta2',
    seriesId: 'macd_histogram_delta2',
    label: 'Δ²H · 加速度',
    formula: 'Δ²H = 本根 ΔH − 上根 ΔH',
    meaning: '正：斜率上升 · 负：斜率下降；转正不代表价格已上涨',
    unit: '价格 / 根²',
  },
] as const

// Both charts use the completed candle window, including MACD warmup gaps.
const orderedChartBars = computed(() => [...bars.value].sort((left, right) => (
  Date.parse(left.bar_start_at) - Date.parse(right.bar_start_at)
)))
const chartTimeAxis = computed(() => {
  const ordered = orderedChartBars.value
  const pointTimes = macdMetrics.flatMap(metric => findMacdSeries(metric.seriesId)?.points ?? [])
    .map(point => Date.parse(point.time)).filter(Number.isFinite)
  if (!ordered.length && !pointTimes.length) return null
  const interval = 5 * 60_000
  const firstTime = ordered.length
    ? Date.parse(ordered[0].bar_start_at) : Math.min(...pointTimes) - interval
  const lastTime = ordered.length
    ? Date.parse(ordered[ordered.length - 1].bar_end_at) : Math.max(...pointTimes)
  const timeSpan = Math.max(lastTime - firstTime, interval)
  const left = 48
  const right = 708
  const plotWidth = right - left
  const xFor = (timestamp: number) => left + ((timestamp - firstTime) / timeSpan) * plotWidth
  const centersByEnd = new Map(ordered.map(bar => [
    Date.parse(bar.bar_end_at),
    (Date.parse(bar.bar_start_at) + Date.parse(bar.bar_end_at)) / 2,
  ]))
  const centerForEnd = (timestamp: number) => centersByEnd.get(timestamp) ?? timestamp - interval / 2
  const tickTimes = [firstTime, firstTime + timeSpan / 2, firstTime + timeSpan]
  return {
    firstTime, lastTime, timeSpan, left, right, plotWidth, xFor, centerForEnd,
    ticks: tickTimes.map((timestamp, index) => ({
      x: xFor(timestamp),
      label: formatEasternTime(new Date(timestamp).toISOString()),
      anchor: index === 0 ? 'start' : index === tickTimes.length - 1 ? 'end' : 'middle',
    })),
  }
})

function buildMacdChart(series: IntradayMacdSeries | null) {
  const axis = chartTimeAxis.value
  if (!axis) return null
  const { firstTime, lastTime, timeSpan, left, right, plotWidth, xFor, centerForEnd } = axis
  const validPoints = (series?.points ?? []).filter((point) => {
    const timestamp = Date.parse(point.time)
    return typeof point.value === 'number' && Number.isFinite(point.value)
      && Number.isFinite(timestamp) && timestamp > firstTime && timestamp <= lastTime
  })
  // An independent symmetric axis keeps zero centered in every lane. Values
  // remain raw; heights across lanes must not be compared as equal units.
  const maximumMagnitude = Math.max(0, ...validPoints.map(point => Math.abs(point.value as number)))
  const scale = maximumMagnitude || 1
  const top = 12
  const bottom = 84
  const zeroY = (top + bottom) / 2
  const yFor = (value: number) => zeroY - value / scale * (bottom - top) / 2
  const chartBars = validPoints.map((point) => {
    const x = xFor(centerForEnd(Date.parse(point.time)))
    const y = yFor(point.value as number)
    const width = Math.min(22, Math.max(3, plotWidth * (5 * 60_000 / timeSpan) * 0.58))
    return {
      key: point.time,
      x: x - width / 2,
      y: Math.min(y, zeroY),
      width,
      height: Math.abs(zeroY - y),
      positive: (point.value as number) >= 0,
      time: point.time,
      value: point.value as number,
    }
  })
  const markers = (series?.markers ?? []).filter(marker => (
    validPoints.some(point => Date.parse(point.time) === Date.parse(marker.time))
  )).map((marker) => ({
    ...marker,
    x: xFor(centerForEnd(Date.parse(marker.time))),
    y: zeroY,
    tone: marker.to_sign === 'positive' ? 'positive' : marker.to_sign === 'negative' ? 'negative' : 'zero',
    title: `${formatEasternTime(marker.time)} ET · ${marker.label} · ${formatNumber(marker.value, 6)}`,
  }))
  return {
    left, right, top, bottom, zeroY, bars: chartBars, markers, ticks: axis.ticks,
    maximumMagnitude,
  }
}

const macdLanes = computed(() => macdMetrics.map(metric => ({
  ...metric,
  value: macd.value?.[metric.key],
  signal: macd.value?.histogram_signals?.[metric.key]?.label ?? '样本不足',
  sign: macd.value?.histogram_signals?.[metric.key]?.sign ?? 'unavailable',
  chart: buildMacdChart(findMacdSeries(metric.seriesId)),
})))

const macdHistogramChart = computed(() => macdLanes.value[0]?.chart ?? null)
const macdSignalMarkers = computed(() => {
  const histogram = macdHistogramChart.value
  if (!histogram) return []
  const barsByTime = new Map(histogram.bars.map(bar => [Date.parse(bar.time), bar]))
  const markers = macdLanes.value.flatMap((lane, laneIndex) => (lane.chart?.markers ?? []).flatMap(marker => {
    const bar = barsByTime.get(Date.parse(marker.time))
    if (!bar) return []
    // All transitions are anchored to the actual H body, not to a different
    // indicator's zero axis. Missing H values never create synthetic bars.
    return [{
      ...marker,
      metric: lane.key,
      shape: laneIndex,
      x: bar.x + bar.width / 2,
      y: bar.y + bar.height * (laneIndex + 1) / 4,
      title: `${lane.label} · ${marker.title}`,
    }]
  }))
  const titlesByTime = new Map<number, string[]>()
  for (const marker of markers) {
    const time = Date.parse(marker.time)
    titlesByTime.set(time, [...(titlesByTime.get(time) ?? []), marker.title])
  }
  return markers.map(marker => ({
    ...marker,
    // Short or zero-height H bodies can make shapes overlap. Hovering any
    // shape must still reveal every transition on that candle.
    title: titlesByTime.get(Date.parse(marker.time))?.join('\n') ?? marker.title,
  }))
})

const macdDerivativeChart = computed(() => {
  const axis = chartTimeAxis.value
  if (!axis) return null
  const lanes = macdLanes.value.slice(1)
  const maximumMagnitude = Math.max(0, ...lanes.flatMap(lane => (
    lane.chart?.bars.map(point => Math.abs(point.value)) ?? []
  )))
  const scale = maximumMagnitude || 1
  const top = 12
  const bottom = 84
  const zeroY = (top + bottom) / 2
  const yFor = (value: number) => zeroY - value / scale * (bottom - top) / 2
  const lines = lanes.map((lane, index) => {
    const points = [...(lane.chart?.bars ?? [])].sort((a, b) => Date.parse(a.time) - Date.parse(b.time))
      .map(point => ({ ...point, x: point.x + point.width / 2, y: yFor(point.value) }))
    const segments: Array<typeof points> = []
    for (const point of points) {
      const segment = segments[segments.length - 1]
      const previous = segment?.[segment.length - 1]
      if (!previous || Date.parse(point.time) - Date.parse(previous.time) !== 5 * 60_000) {
        segments.push([point])
      } else {
        segment.push(point)
      }
    }
    return {
      ...lane,
      color: index === 0 ? '#69b5e8' : '#e5b455',
      paths: segments.filter(segment => segment.length > 1).map(segment => (
        segment.map((point, i) => `${i ? 'L' : 'M'} ${point.x} ${point.y}`).join(' ')
      )),
      points: segments.flatMap(segment => segment.map(point => ({ ...point, singleton: segment.length === 1 }))),
    }
  })
  return {
    left: axis.left, right: axis.right, top, bottom, zeroY, maximumMagnitude,
    ticks: axis.ticks, lines,
    availableCount: lines.reduce((count, line) => count + line.points.length, 0),
  }
})

const candleChart = computed(() => {
  const orderedBars = orderedChartBars.value
  const axis = chartTimeAxis.value
  if (!orderedBars.length || !axis) return null
  const { timeSpan, left, right, plotWidth, xFor } = axis
  const top = 16
  const bottom = 192
  const plotHeight = bottom - top

  const movingAverages: Array<{ timestamp: number; value: number } | null> = orderedBars.map((bar, index) => {
    if (index < 4) return null
    const windowBars = orderedBars.slice(index - 4, index + 1)
    const continuous = windowBars.every((item, windowIndex) => (
      windowIndex === 0
      || Date.parse(windowBars[windowIndex - 1].bar_end_at) === Date.parse(item.bar_start_at)
    ))
    if (!continuous) return null
    return {
      // Align with the candle center; the value still uses completed bars.
      timestamp: (Date.parse(bar.bar_start_at) + Date.parse(bar.bar_end_at)) / 2,
      value: windowBars.reduce((total, item) => total + item.close, 0) / 5,
    }
  })
  const frozenPrices = frozenLevels.value.flatMap((item) => [
    item.confirmationLevel,
    item.invalidationLevel,
  ]).filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
  const allValues = orderedBars.flatMap((bar) => [bar.high, bar.low])
    .concat(movingAverages.flatMap((item) => item ? [item.value] : []), frozenPrices)
  const minimum = Math.min(...allValues)
  const maximum = Math.max(...allValues)
  const padding = (maximum - minimum || maximum * 0.01 || 1) * 0.08
  const chartMinimum = minimum - padding
  const chartMaximum = maximum + padding
  const valueSpan = chartMaximum - chartMinimum || 1
  const yFor = (value: number) => bottom - ((value - chartMinimum) / valueSpan) * plotHeight
  const candleWidth = Math.min(22, Math.max(3, plotWidth * (5 * 60_000 / timeSpan) * 0.62))
  const candles = orderedBars.map((bar) => {
    const start = Date.parse(bar.bar_start_at)
    const end = Date.parse(bar.bar_end_at)
    const x = xFor((start + end) / 2)
    const openY = yFor(bar.open)
    const closeY = yFor(bar.close)
    return {
      key: bar.bar_start_at,
      time: `${formatEasternTime(bar.bar_start_at)}–${formatEasternTime(bar.bar_end_at)} ET`,
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
      x,
      width: candleWidth,
      highY: yFor(bar.high),
      lowY: yFor(bar.low),
      bodyY: Math.min(openY, closeY),
      bodyHeight: Math.max(1.5, Math.abs(closeY - openY)),
      up: bar.close >= bar.open,
    }
  })

  const maSegments: Array<Array<{ x: number; y: number }>> = []
  let activeSegment: Array<{ x: number; y: number }> = []
  movingAverages.forEach((item, index) => {
    if (!item) {
      if (activeSegment.length) maSegments.push(activeSegment)
      activeSegment = []
      return
    }
    const previousBar = orderedBars[index - 1]
    const currentBar = orderedBars[index]
    const followsGap = previousBar
      && Date.parse(previousBar.bar_end_at) !== Date.parse(currentBar.bar_start_at)
    if (followsGap && activeSegment.length) {
      maSegments.push(activeSegment)
      activeSegment = []
    }
    activeSegment.push({ x: xFor(item.timestamp), y: yFor(item.value) })
  })
  if (activeSegment.length) maSegments.push(activeSegment)

  const priceTicks = [chartMinimum, (chartMinimum + chartMaximum) / 2, chartMaximum]
  const setupLines = frozenLevels.value.flatMap((item) => {
    const lines: Array<{ key: string; y: number; label: string; kind: string }> = []
    if (item.confirmationLevel !== null) {
      lines.push({
        key: `${item.setupId}:confirmation`,
        y: yFor(item.confirmationLevel),
        label: `确认 ${formatPrice(item.confirmationLevel)}`,
        kind: 'confirmation',
      })
    }
    if (item.invalidationLevel !== null) {
      lines.push({
        key: `${item.setupId}:invalidation`,
        y: yFor(item.invalidationLevel),
        label: `失效 ${formatPrice(item.invalidationLevel)}`,
        kind: 'invalidation',
      })
    }
    return lines
  })
  return {
    left,
    right,
    top,
    bottom,
    candles,
    maSegments,
    ticks: axis.ticks,
    priceTicks: priceTicks.map((value) => ({ y: yFor(value), label: formatPrice(value) })),
    setupLines,
  }
})

function selectAvailableSymbol(rows: IntradaySymbolProjection[]) {
  if (selectedSymbol.value && rows.some((item) => item.symbol === selectedSymbol.value)) return
  selectedSymbol.value = rows[0]?.symbol ?? ''
}

async function loadOverview() {
  const generation = ++overviewGeneration
  overviewController?.abort()
  overviewController = new AbortController()
  loading.value = true
  error.value = ''
  try {
    const result = await getIntradayOverview(overviewController.signal)
    if (generation !== overviewGeneration || overviewController.signal.aborted) return
    overview.value = result
    selectAvailableSymbol(filteredSymbols.value)
    if (!symbolRows.value.length) selectedSymbol.value = ''
  } catch (reason) {
    if (generation === overviewGeneration && !overviewController.signal.aborted) {
      error.value = reason instanceof Error ? reason.message : '盘中观察读取失败。'
    }
  } finally {
    if (generation === overviewGeneration) loading.value = false
  }
}

function refreshPageData() {
  if (loading.value) return
  refreshToken.value += 1
  void loadOverview()
}

function scheduleRefresh() {
  if (refreshTimer !== undefined) window.clearTimeout(refreshTimer)
  // Wall-clock boundaries avoid drift from the time the page was opened.
  const delay = REFRESH_INTERVAL_MS - (Date.now() % REFRESH_INTERVAL_MS)
  refreshTimer = window.setTimeout(() => {
    if (!document.hidden) refreshPageData()
    scheduleRefresh()
  }, delay)
}

function handleVisibilityChange() {
  if (document.hidden) return
  refreshPageData()
  scheduleRefresh()
}

watch(roleFilter, () => { selectAvailableSymbol(filteredSymbols.value) })
watch(poolQuery, () => {
  if (!poolQuery.value.trim()) return
  for (const group of poolGroups.value) expandedPoolGroups.value[group.name] = true
})

onMounted(() => {
  void loadOverview()
  scheduleRefresh()
  document.addEventListener('visibilitychange', handleVisibilityChange)
})
onBeforeUnmount(() => {
  overviewController?.abort()
  if (refreshTimer !== undefined) window.clearTimeout(refreshTimer)
  document.removeEventListener('visibilitychange', handleVisibilityChange)
})
</script>

<template>
  <AppShell />
  <main class="page-shell intraday-page">
    <header class="intraday-page-header">
      <div>
        <p class="eyebrow">INTRADAY OBSERVATION</p>
        <h1>日内<em>观察</em></h1>
        <p class="intraday-intro">查看最新已采集的美股行情、完成 5 分钟 K 与连续区间指标。刷新只读取已采集数据，不发起行情请求。</p>
      </div>
      <div class="intraday-refresh-control">
        <small>每 5 分钟整点（ET）自动读取</small>
        <button class="secondary-button" type="button" :disabled="loading" @click="refreshPageData">
          {{ loading ? '刷新中…' : '刷新' }}
        </button>
      </div>
    </header>

    <section
      v-if="overview"
      class="intraday-source-panel"
      :data-status="overview.status"
      aria-label="最新采集状态"
    >
      <div class="intraday-source-status">
        <span class="intraday-state-dot" aria-hidden="true"></span>
        <div>
          <strong>{{ statusLabel(overview.status) }}</strong>
          <small>{{ overview.warnings[0] ? displayWarning(overview.warnings[0]) : '当前页面只读取已采集数据。' }}</small>
        </div>
      </div>
      <div v-if="overview.source" class="intraday-source-meta">
        <span>交易日 <strong>{{ overview.source.trading_date ?? '—' }}</strong></span>
        <span>调度槽 <strong>{{ formatEastern(overview.source.scheduled_at) }} ET</strong></span>
        <span>证据截止 <strong>{{ formatEastern(overview.source.cutoff) }} ET</strong></span>
      </div>
      <div v-else class="intraday-source-meta"><span>未使用示例行情填充数据。</span></div>
      <div v-if="overview.source" class="intraday-source-submeta">
        <span>数据新鲜度 {{ statusLabel(overview.freshness.status) }} · {{ Math.round(overview.freshness.age_seconds ?? 0) }} 秒</span>
        <details class="intraday-source-details">
          <summary>数据来源与版本</summary>
          <span>采集编号 {{ overview.source.capture_id ?? '—' }}</span>
          <span>内容校验 {{ overview.source.content_sha256 ?? '—' }}</span>
          <span>采集时间 {{ formatEastern(overview.source.captured_at) }} ET</span>
          <span v-if="overview.source.stored_at">保存时间 {{ formatEastern(overview.source.stored_at) }} ET</span>
          <span>{{ overview.source.schema_version ?? '—' }} · {{ overview.formula_version }}</span>
        </details>
      </div>
    </section>

    <section v-if="error" class="intraday-error" role="alert">{{ error }}</section>
    <section v-if="loading && !overview" class="empty-panel intraday-empty"><p>正在读取最新已采集数据…</p></section>
    <section v-else-if="overview && ['disabled', 'no_data', 'invalid_source'].includes(overview.status)" class="empty-panel intraday-empty">
      <p class="eyebrow">{{ overview.status.replace('_', ' ').toUpperCase() }}</p>
      <h2>{{ statusLabel(overview.status) }}</h2>
      <p>{{ overview.warnings.map(displayWarning).join(' ') }}</p>
      <small>刷新只重新读取已采集数据，不会发起新的行情采集。</small>
    </section>

    <template v-else-if="overview">
      <section class="intraday-breadth-grid" aria-label="观察池广度">
        <article class="intraday-breadth-card">
          <span>观察池</span>
          <strong>{{ overview.breadth.effective_denominator ?? 0 }} / {{ overview.breadth.requested_count ?? 0 }}</strong>
          <small>有效分钟线 / 配置标的 · 截止 {{ formatEastern(overview.breadth.as_of) }} ET</small>
        </article>
        <article class="intraday-breadth-card">
          <span>观察池上涨</span>
          <strong>{{ formatRatio(overview.breadth.up_fraction) }}</strong>
          <small>{{ overview.breadth.up_count ?? 0 }} 涨 · {{ overview.breadth.down_count ?? 0 }} 跌 · {{ overview.breadth.flat_count ?? 0 }} 平</small>
        </article>
        <article class="intraday-breadth-card">
          <span>近 30 分钟上涨</span>
          <strong>{{ formatRatio(overview.breadth.up_over_30m?.fraction) }}</strong>
          <small>有效分母 {{ overview.breadth.up_over_30m?.effective_denominator ?? 0 }}</small>
        </article>
        <article class="intraday-breadth-card">
          <span>高于 Session VWAP</span>
          <strong>{{ formatRatio(overview.breadth.above_session_vwap?.fraction) }}</strong>
          <small>有效分母 {{ overview.breadth.above_session_vwap?.effective_denominator ?? 0 }}</small>
        </article>
      </section>

      <details v-if="overview.warnings.length > 1" class="intraday-warning-list" aria-label="数据提示">
        <summary>数据提示 · {{ overview.warnings.length }} 条</summary>
        <p v-for="warning in overview.warnings" :key="warning">{{ displayWarning(warning) }}</p>
      </details>

    </template>
    <IntradayEventPanel
      v-if="overview"
      :selected-symbol="selectedSymbol"
      :trading-date="overview.source?.trading_date ?? ''"
      :refresh-token="refreshToken"
      @frozen-levels="frozenLevels = $event"
    />

    <template v-if="overview && !['disabled', 'no_data', 'invalid_source'].includes(overview.status)">
      <section class="intraday-workspace" aria-label="盘中标的观察">
        <aside class="intraday-symbol-panel">
          <div class="intraday-panel-heading">
            <h2>标的池 <small>{{ symbolRows.length }}</small></h2>
            <RouterLink to="/settings/universe#intraday-pool">设置</RouterLink>
          </div>
          <label class="intraday-pool-search">
            <input v-model="poolQuery" type="search" placeholder="搜索代码 / 分组" aria-label="搜索标的或分组" />
          </label>
          <details class="intraday-pool-options">
            <summary>采集角色筛选</summary>
            <label class="intraday-role-filter">
              <select v-model="roleFilter" aria-label="筛选采集角色">
                <option value="all">全部</option>
                <option value="A">大盘 ETF (A)</option>
                <option value="B">板块 ETF (B)</option>
                <option value="C">重点股 (C)</option>
                <option value="D">轮换股 (D)</option>
              </select>
            </label>
          </details>
          <div class="intraday-pool-groups">
            <div v-if="!filteredSymbols.length" class="intraday-list-empty">没有匹配的标的。</div>
            <section v-for="group in poolGroups" :key="group.name" class="intraday-pool-group">
              <button class="intraday-pool-group-toggle" type="button" :aria-expanded="isPoolGroupOpen(group)" @click="togglePoolGroup(group)">
                <span>{{ group.name }}</span><small>{{ group.members.length }} {{ isPoolGroupOpen(group) ? '−' : '+' }}</small>
              </button>
              <div v-if="isPoolGroupOpen(group)">
                <button
                  v-for="item in group.members"
                  :key="item.symbol"
                  class="intraday-symbol-row"
                  :class="{ selected: item.symbol === selectedSymbol }"
                  :aria-pressed="item.symbol === selectedSymbol"
                  type="button"
                  @click="selectedSymbol = item.symbol"
                >
                  <span class="intraday-symbol-primary"><strong>{{ item.symbol }}</strong><small>{{ statusLabel(item.status) }}</small></span>
                  <span class="intraday-symbol-price">{{ formatPrice(item.quote?.last_price) }}</span>
                </button>
              </div>
            </section>
          </div>
        </aside>

        <section class="intraday-detail-panel" aria-label="所选标的详情">
          <div v-if="!selectedSymbol" class="intraday-detail-empty">
            <span class="eyebrow">NO SYMBOL SELECTED</span>
            <h2>暂无可展示标的</h2>
            <p>最新采集槽没有报价或分钟线标的。</p>
          </div>
          <template v-else>
            <header class="intraday-detail-heading">
              <div>
                <p class="section-kicker">{{ selectedSummary ? poolGroupName(selectedSummary) : 'INTRADAY SYMBOL' }}</p>
                <div class="intraday-instrument-title"><h2>{{ selectedSymbol }}</h2><strong>{{ formatPrice(selectedQuote?.last_price) }}</strong><small>最新报价</small></div>
              </div>
              <span class="intraday-status-pill" :data-status="currentStatus">{{ statusLabel(currentStatus) }}</span>
            </header>

            <details class="intraday-quote-details">
              <summary>报价时间 <span>{{ selectedQuote?.quote_time_status === 'known' ? `${formatEastern(selectedQuote.quote_time)} ET` : '来源时间未知' }}</span></summary>
              <section class="intraday-quote-strip" aria-label="报价时间">
              <div><span>报价来源时间</span><strong :title="selectedQuote?.quote_time_raw ?? undefined">{{ selectedQuote?.quote_time_status === 'known' ? `${formatEastern(selectedQuote.quote_time)} ET` : '未知' }}</strong><small>{{ selectedQuote?.quote_time_raw ? `原始时间 ${selectedQuote.quote_time_raw}` : '来源时间不可用' }}</small></div>
              <div><span>收到时间</span><strong>{{ selectedQuote?.received_at ? `${formatEastern(selectedQuote.received_at)} ET` : '未知' }}</strong><small>采集接收记录</small></div>
              </section>
            </details>

            <div v-if="features" class="intraday-feature-grid">
              <article class="intraday-feature-card">
                <span>MA5</span><strong>{{ formatPrice(features.ma5) }}</strong>
                <small>距 MA5 {{ formatPercent(features.ma5_distance_pct) }}</small>
                <small>斜率 {{ formatNumber(features.ma5_slope, 4) }}</small>
              </article>
              <article class="intraday-feature-card">
                <span>Wilder ATR14</span><strong>{{ formatPrice(features.atr14) }}</strong>
                <small>5 分钟波幅</small>
              </article>
              <article class="intraday-feature-card">
                <span>Session VWAP</span><strong>{{ formatPrice(features.session_vwap) }}</strong>
                <small>当日成交均价</small>
              </article>
              <article class="intraday-feature-card">
                <span>RS15 / RS30</span><strong>{{ formatPercent(features.rs15) }} / {{ formatPercent(features.rs30) }}</strong>
                <small>相对 SPY 收益差</small>
              </article>
            </div>

            <details class="intraday-metric-details">
              <summary>指标说明与缺失原因</summary>
              <p>MA5 为最近 5 根有效 5 分钟 K 的均价；ATR14 衡量波幅；VWAP 从当天开盘累计；RS 比较同区间内相对 SPY 的收益差。</p>
              <p v-for="key in ['ma5', 'atr14', 'session_vwap', 'rs15', 'rs30']" :key="key" v-show="metricReason(key)">{{ key.toUpperCase() }}：{{ metricReason(key) }}</p>
            </details>

            <section v-if="macd" class="intraday-macd-card">
              <div class="intraday-section-heading">
                <div><span class="section-kicker">MACD 12 / 26 / 9</span><h3>动量三层</h3></div>
                <span class="intraday-warmup" :data-ready="features?.macd_signal_eligible">
                  {{ features?.macd_warmup_bars ?? 0 }} / 100 根 · {{ features?.macd_signal_eligible ? '策略预热完成' : '预热中，不可触发策略' }}
                </span>
              </div>
              <div class="intraday-macd-summary">
                <div v-for="lane in macdLanes" :key="lane.key" class="intraday-macd-summary-item" :data-metric="lane.key">
                  <span>{{ lane.label }}</span>
                  <div class="intraday-macd-lane-current" :data-sign="lane.sign"><strong>{{ formatNumber(lane.value, 6) }}</strong><small>{{ lane.signal }}</small></div>
                </div>
              </div>
              <section class="intraday-macd-histogram-pane" aria-label="MACD H 柱状图与三项过零信号">
                <div class="intraday-macd-pane-heading"><h4>H · MACD 柱状图</h4><small>绿色为正 · 红色为负</small></div>
                <div v-if="macdHistogramChart" class="intraday-macd-chart-wrap">
                  <svg viewBox="0 0 720 120" class="intraday-macd-chart intraday-macd-histogram-chart" role="img" aria-label="MACD H 柱状图，三项过零标记位于柱体，美东时间" preserveAspectRatio="none">
                    <line :x1="macdHistogramChart.left" :y1="macdHistogramChart.top" :x2="macdHistogramChart.right" :y2="macdHistogramChart.top" class="intraday-chart-gridline" />
                    <line :x1="macdHistogramChart.left" :y1="macdHistogramChart.bottom" :x2="macdHistogramChart.right" :y2="macdHistogramChart.bottom" class="intraday-chart-gridline" />
                    <line :x1="macdHistogramChart.left" :y1="macdHistogramChart.zeroY" :x2="macdHistogramChart.right" :y2="macdHistogramChart.zeroY" class="intraday-zero-line" />
                    <text v-if="macdHistogramChart.maximumMagnitude" x="2" :y="macdHistogramChart.top + 3" class="intraday-price-label">+{{ formatNumber(macdHistogramChart.maximumMagnitude, 6) }}</text>
                    <text x="2" :y="macdHistogramChart.zeroY + 3" class="intraday-price-label">0</text>
                    <text v-if="macdHistogramChart.maximumMagnitude" x="2" :y="macdHistogramChart.bottom + 3" class="intraday-price-label">−{{ formatNumber(macdHistogramChart.maximumMagnitude, 6) }}</text>
                    <rect v-for="bar in macdHistogramChart.bars" :key="bar.key" :x="bar.x" :y="bar.y" :width="bar.width" :height="bar.height" :class="bar.positive ? 'intraday-macd-positive' : 'intraday-macd-negative'"><title>H · {{ formatEasternTime(bar.time) }} ET · {{ formatNumber(bar.value, 6) }}</title></rect>
                    <g v-for="marker in macdSignalMarkers" :key="`${marker.metric}:${marker.time}:${marker.type}`" class="intraday-macd-signal-marker" :data-metric="marker.metric" :class="`intraday-marker-${marker.tone}`">
                      <title>{{ marker.title }}</title>
                      <circle v-if="marker.shape === 0" :cx="marker.x" :cy="marker.y" r="4" />
                      <path v-else-if="marker.shape === 1" :d="`M ${marker.x} ${marker.y - 4} L ${marker.x + 4} ${marker.y + 3} L ${marker.x - 4} ${marker.y + 3} Z`" />
                      <path v-else :d="`M ${marker.x} ${marker.y - 4} L ${marker.x + 4} ${marker.y} L ${marker.x} ${marker.y + 4} L ${marker.x - 4} ${marker.y} Z`" />
                    </g>
                    <text v-if="!macdHistogramChart.bars.length" x="378" y="38" text-anchor="middle" class="intraday-axis-label">样本不足，尚无可显示的指标值</text>
                    <text v-for="tick in macdHistogramChart.ticks" :key="tick.label" :x="tick.x" y="111" class="intraday-axis-label" :text-anchor="tick.anchor">{{ tick.label }}</text>
                  </svg>
                </div>
                <p v-else class="intraday-list-empty">尚无可用的时间序列。</p>
                <p class="intraday-macd-marker-key">柱体过零标记：○ H · △ 斜率 · ◇ 加速度；悬停查看同柱全部信号</p>
              </section>
              <section class="intraday-macd-derivative-pane" aria-label="斜率与加速度叠加曲线">
                <div class="intraday-macd-pane-heading"><h4>斜率 + 加速度</h4><div class="intraday-macd-curve-legend"><span class="slope">━ ΔH 斜率</span><span class="acceleration">━ Δ²H 加速度</span></div></div>
                <div v-if="macdDerivativeChart" class="intraday-macd-chart-wrap">
                  <svg viewBox="0 0 720 120" class="intraday-macd-chart intraday-macd-derivative-chart" role="img" aria-label="ΔH 斜率与 Δ²H 加速度叠加曲线，共用原始数值刻度，美东时间" preserveAspectRatio="none">
                    <line :x1="macdDerivativeChart.left" :y1="macdDerivativeChart.top" :x2="macdDerivativeChart.right" :y2="macdDerivativeChart.top" class="intraday-chart-gridline" />
                    <line :x1="macdDerivativeChart.left" :y1="macdDerivativeChart.bottom" :x2="macdDerivativeChart.right" :y2="macdDerivativeChart.bottom" class="intraday-chart-gridline" />
                    <line :x1="macdDerivativeChart.left" :y1="macdDerivativeChart.zeroY" :x2="macdDerivativeChart.right" :y2="macdDerivativeChart.zeroY" class="intraday-zero-line" />
                    <text v-if="macdDerivativeChart.maximumMagnitude" x="2" :y="macdDerivativeChart.top + 3" class="intraday-price-label">+{{ formatNumber(macdDerivativeChart.maximumMagnitude, 6) }}</text>
                    <text x="2" :y="macdDerivativeChart.zeroY + 3" class="intraday-price-label">0</text>
                    <text v-if="macdDerivativeChart.maximumMagnitude" x="2" :y="macdDerivativeChart.bottom + 3" class="intraday-price-label">−{{ formatNumber(macdDerivativeChart.maximumMagnitude, 6) }}</text>
                    <g v-for="line in macdDerivativeChart.lines" :key="line.key" :data-metric="line.key" class="intraday-macd-derivative-line">
                      <path v-for="(path, index) in line.paths" :key="index" :d="path" :stroke="line.color" class="intraday-derivative-path" />
                      <circle v-for="point in line.points" :key="point.time" :cx="point.x" :cy="point.y" r="2.5" :fill="line.color" class="intraday-derivative-point" :class="{ 'is-singleton': point.singleton }"><title>{{ line.label }} · {{ formatEasternTime(point.time) }} ET · {{ formatNumber(point.value, 6) }}</title></circle>
                    </g>
                    <text v-if="!macdDerivativeChart.availableCount" x="378" y="38" text-anchor="middle" class="intraday-axis-label">样本不足，尚无可显示的指标值</text>
                    <text v-for="tick in macdDerivativeChart.ticks" :key="tick.label" :x="tick.x" y="111" class="intraday-axis-label" :text-anchor="tick.anchor">{{ tick.label }}</text>
                  </svg>
                </div>
                <p v-else class="intraday-list-empty">尚无可用的时间序列。</p>
                <p class="intraday-macd-reading-guide">双线共用数值刻度，保留原始差分值；量纲不同，不直接比较强弱。</p>
              </section>
              <div class="intraday-chart-caption">{{ overview.source?.trading_date ?? '—' }} · 5 分钟 · 美东 HH:mm · 空白表示样本不足或数据缺失</div>
              <details class="intraday-metric-details">
                <summary>三层公式与读法</summary>
                <p v-for="metric in macdMetrics" :key="metric.key">{{ metric.formula }}。{{ metric.meaning }}。</p>
                <p>H 的正负不是价格涨跌；Δ²H 转正仅表示斜率在上升，不能单独作为买卖信号。过零标记绿色为转正、红色为转负，中性色为归零。</p>
              </details>
            </section>

            <section class="intraday-bars-section">
              <div class="intraday-section-heading">
                <div><span class="section-kicker">COMPLETED SESSION-ANCHORED BARS</span><h3>5 分钟 K 与 MA5</h3></div>
                <small>{{ overview.source?.trading_date ?? '—' }} · 截止 {{ formatEastern(selectedSummary?.as_of) }} ET · {{ bars.length }} 根</small>
              </div>
              <div v-if="!bars.length" class="intraday-list-empty">
                {{ currentStatus === 'quote_only' ? '本轮只采报价，分钟线按 D 组轮换。' : '没有可用的完整 5 分钟区间。' }}
              </div>
              <div v-else>
                <div v-if="candleChart" class="intraday-candle-chart-wrap">
                  <svg viewBox="0 0 720 232" class="intraday-candle-chart" role="img" :aria-label="`${selectedSymbol} 5 分钟蜡烛图和 MA5，美东时间`" preserveAspectRatio="none">
                    <line v-for="tick in candleChart.priceTicks" :key="`price:${tick.label}`" :x1="candleChart.left" :y1="tick.y" :x2="candleChart.right" :y2="tick.y" class="intraday-chart-gridline" />
                    <text v-for="tick in candleChart.priceTicks" :key="`price-label:${tick.label}`" x="2" :y="tick.y - 3" class="intraday-price-label">{{ tick.label }}</text>
                    <g v-for="candle in candleChart.candles" :key="candle.key" :class="candle.up ? 'intraday-candle-up' : 'intraday-candle-down'">
                      <title>{{ candle.time }} · 开 {{ formatPrice(candle.open) }} · 高 {{ formatPrice(candle.high) }} · 低 {{ formatPrice(candle.low) }} · 收 {{ formatPrice(candle.close) }}</title>
                      <line :x1="candle.x" :y1="candle.highY" :x2="candle.x" :y2="candle.lowY" class="intraday-candle-wick" />
                      <rect :x="candle.x - candle.width / 2" :y="candle.bodyY" :width="candle.width" :height="candle.bodyHeight" rx="1" class="intraday-candle-body" />
                    </g>
                    <g v-for="(segment, index) in candleChart.maSegments" :key="`ma5:${index}`">
                      <polyline :points="segment.map(point => `${point.x},${point.y}`).join(' ')" class="intraday-ma5-line" />
                      <circle v-for="point in segment" :key="`${point.x}:${point.y}`" :cx="point.x" :cy="point.y" r="1.6" class="intraday-ma5-point" />
                    </g>
                    <g v-for="line in candleChart.setupLines" :key="line.key">
                      <line :x1="candleChart.left" :y1="line.y" :x2="candleChart.right" :y2="line.y" :class="`intraday-event-level-${line.kind}`" />
                      <text :x="candleChart.right - 4" :y="line.y - 3" text-anchor="end" class="intraday-event-level-label">{{ line.label }}</text>
                    </g>
                    <text v-for="tick in candleChart.ticks" :key="`time:${tick.label}`" :x="tick.x" y="222" class="intraday-axis-label" :text-anchor="tick.anchor">{{ tick.label }}</text>
                  </svg>
                  <div class="intraday-chart-caption">{{ overview.source?.trading_date ?? '—' }} · 5 分钟 · 美东 HH:mm · 缺口按真实时间留白</div>
                  <div class="intraday-chart-legend"><span><i class="intraday-legend-candle"></i>蜡烛线</span><span><i class="intraday-legend-ma5"></i>MA5</span></div>
                </div>
                <div class="intraday-bars-table-wrap">
                  <table class="intraday-bars-table">
                    <thead><tr><th>美东区间</th><th>开</th><th>高</th><th>低</th><th>收</th><th>成交量</th><th>成交额</th></tr></thead>
                    <tbody>
                      <tr v-for="bar in [...bars].reverse()" :key="bar.bar_start_at">
                        <td>{{ formatEastern(bar.bar_start_at) }}–{{ formatEastern(bar.bar_end_at) }}</td>
                        <td>{{ formatPrice(bar.open) }}</td><td>{{ formatPrice(bar.high) }}</td><td>{{ formatPrice(bar.low) }}</td><td>{{ formatPrice(bar.close) }}</td>
                        <td>{{ Math.round(bar.volume).toLocaleString() }}</td><td>{{ Math.round(bar.turnover).toLocaleString() }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
            </section>
          </template>
        </section>
      </section>
    </template>
    <SystematicRiskCard :refresh-token="refreshToken" />
  </main>
</template>
