<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { api } from '@/api/client'
import AppShell from '@/components/AppShell.vue'
import DecisionChartWorkspace from '@/components/decision/DecisionChartWorkspace.vue'
import type {
  ChartPoint,
  ChartSeries,
  DailyBar,
  DailyDecisionDataset,
  DailyEvidenceResponse,
  DecisionChartProjection,
  DeterministicSynthesis,
  StrategyDecision,
} from '@/types/dailyEvidence'

type LayerKey = 'ma20' | 'ma50' | 'ma200' | 'bollinger' | 'volume' | 'rsi' | 'macd' | 'relative'
type WatchGroup = {
  id: string
  name: string
  benchmark: string
  symbols: string[]
  source: string
  tags: string[]
}

const router = useRouter()
const route = useRoute()
const selectedSymbol = ref(String(route.params.symbol ?? 'INTC').toUpperCase())
const symbolDraft = ref(selectedSymbol.value)
const range = ref<'3M' | '6M' | '1Y' | 'ALL'>('6M')
const cursorIndex = ref<number | null>(null)
const loading = ref(false)
const error = ref('')
const demoReason = ref('')
const dataset = ref<DailyDecisionDataset | null>(null)
const projection = ref<DecisionChartProjection | null>(null)
const strategyDecisions = ref<StrategyDecision[]>([])
const deterministicSynthesis = ref<DeterministicSynthesis>({})
const activeStrategy = ref<string | null>(null)
const aiNotice = ref('')
const showTechnicalDetails = ref(false)
const layers = reactive<Record<LayerKey, boolean>>({
  ma20: true,
  ma50: true,
  ma200: true,
  bollinger: false,
  volume: true,
  rsi: true,
  macd: false,
  relative: true,
})

const defaultWatchGroups: WatchGroup[] = [
  { id: 'semiconductors', name: '半导体', benchmark: 'SOXX', symbols: ['INTC', 'NVDA', 'AMD'], source: 'manual', tags: ['theme'] },
  { id: 'optical-modules', name: '光模块', benchmark: 'QQQ', symbols: ['LITE', 'COHR'], source: 'manual', tags: ['theme'] },
]
const watchGroups = ref<WatchGroup[]>([])
const expandedGroups = reactive<Record<string, boolean>>({})
const isIndicatorGroup = (group: WatchGroup) => group.source === 'universe' && (
  group.tags.includes('indicator-recommendation') || group.tags.includes('watchlist')
)
const isLegacySelfSelectedGroup = (group: WatchGroup) => group.source === 'manual' && (
  group.tags.includes('self-selected') || group.tags.includes('user-selected') || group.tags.includes('user-qualified')
)
const indicatorGroups = computed(() => watchGroups.value.filter(isIndicatorGroup))
const sectorGroups = computed(() => watchGroups.value.filter((group) => !isIndicatorGroup(group) && !isLegacySelfSelectedGroup(group)))
const indicatorSymbolCount = computed(() => indicatorGroups.value.reduce((count, group) => count + group.symbols.length, 0))
const sectorSymbolCount = computed(() => sectorGroups.value.reduce((count, group) => count + group.symbols.length, 0))
const groupDisplayName = (group: WatchGroup) => {
  if (isIndicatorGroup(group)) return '指标推荐'
  return group.name
}
const groupSubtitle = (group: WatchGroup) => {
  if (isIndicatorGroup(group)) return `${group.benchmark} benchmark · 自动生成`
  return `${group.benchmark} benchmark`
}
const selectedGroup = computed<WatchGroup>(() => sectorGroups.value.find((group) => group.symbols.includes(selectedSymbol.value))
  ?? indicatorGroups.value.find((group) => group.symbols.includes(selectedSymbol.value))
  ?? {
  id: 'unassigned',
  name: '未分组',
  benchmark: 'QQQ',
  symbols: [selectedSymbol.value],
  source: 'manual',
  tags: [],
})
const layerLabels: Array<{ key: LayerKey; label: string; short: string }> = [
  { key: 'ma20', label: 'MA20', short: '20' },
  { key: 'ma50', label: 'MA50', short: '50' },
  { key: 'ma200', label: 'MA200', short: '200' },
  { key: 'bollinger', label: 'Bollinger', short: 'BB' },
  { key: 'volume', label: 'Volume', short: 'VOL' },
  { key: 'rsi', label: 'RSI14', short: 'RSI' },
  { key: 'macd', label: 'MACD', short: 'MACD' },
  { key: 'relative', label: 'vs QQQ', short: 'REL' },
]

const bars = computed(() => projection.value?.instruments?.[selectedSymbol.value]?.price?.bars ?? [])
const quality = computed(() => dataset.value?.quality?.symbols?.[selectedSymbol.value] ?? null)
const latestBar = computed(() => bars.value[bars.value.length - 1] ?? null)
const previousBar = computed(() => bars.value[bars.value.length - 2] ?? null)
const latestChange = computed(() => {
  if (!latestBar.value || !previousBar.value || previousBar.value.close === 0) return null
  return ((latestBar.value.close / previousBar.value.close) - 1) * 100
})

function latestSeriesValue(seriesId: string): number | null {
  const item = projection.value?.instruments?.[selectedSymbol.value]?.series?.find((entry) => entry.series_id === seriesId)
  if (!item) return null
  const points = [...item.points].reverse()
  const point = points.find((entry) => typeof entry.value === 'number')
  return point?.value ?? null
}

const ma20 = computed(() => latestSeriesValue('ma20'))
const ma50 = computed(() => latestSeriesValue('ma50'))
const ma200 = computed(() => latestSeriesValue('ma200'))
const rsi = computed(() => latestSeriesValue('rsi14'))
const macdHistogram = computed(() => latestSeriesValue('macd_histogram'))
const relative = computed(() => {
  const series = projection.value?.instruments?.[selectedSymbol.value]?.series?.find((entry) => entry.series_id.startsWith('relative_performance'))
  if (!series) return null
  return [...series.points].reverse().find((point) => typeof point.value === 'number')?.value ?? null
})

const trend = computed(() => {
  const close = latestBar.value?.close
  if (close === undefined || ma20.value === null || ma50.value === null) return { label: '等待均线', tone: 'neutral', detail: '指标样本尚未完整' }
  if (close > ma20.value && ma20.value > ma50.value) return { label: '趋势偏强', tone: 'positive', detail: '价格位于 MA20 / MA50 上方' }
  if (close < ma20.value && ma20.value < ma50.value) return { label: '趋势偏弱', tone: 'negative', detail: '价格位于 MA20 / MA50 下方' }
  return { label: '结构盘整', tone: 'neutral', detail: '均线尚未形成方向一致排列' }
})

const momentum = computed(() => {
  if (rsi.value === null && macdHistogram.value === null) return { label: '等待动量', tone: 'neutral', detail: 'RSI / MACD 尚未可用' }
  if (rsi.value !== null && rsi.value >= 70) return { label: '动量过热', tone: 'warning', detail: `RSI14 ${rsi.value.toFixed(1)}，需防止追高` }
  if (rsi.value !== null && rsi.value <= 30) return { label: '动量超卖', tone: 'warning', detail: `RSI14 ${rsi.value.toFixed(1)}，等待反转确认` }
  if (macdHistogram.value !== null && macdHistogram.value > 0) return { label: '动量改善', tone: 'positive', detail: 'MACD 柱体位于零轴上方' }
  return { label: '动量偏弱', tone: 'negative', detail: 'MACD 柱体位于零轴下方' }
})

const relativeState = computed(() => {
  if (relative.value === null) return { label: '暂无相对强弱', tone: 'neutral', detail: '需要 QQQ benchmark' }
  if (relative.value > 102) return { label: '跑赢 QQQ', tone: 'positive', detail: `相对指数 ${relative.value.toFixed(1)}` }
  if (relative.value < 98) return { label: '落后 QQQ', tone: 'negative', detail: `相对指数 ${relative.value.toFixed(1)}` }
  return { label: '接近基准', tone: 'neutral', detail: `相对指数 ${relative.value.toFixed(1)}` }
})

const qualityState = computed(() => {
  const status = quality.value?.status ?? dataset.value?.status ?? 'missing'
  const label: Record<string, string> = { ok: '完整', partial: '部分完整', stale: '数据滞后', missing: '缺失', conflicted: '冲突' }
  return { label: label[status] ?? status, tone: status === 'ok' ? 'positive' : status === 'partial' || status === 'stale' ? 'warning' : 'negative' }
})

const sourceLabel = computed(() => dataset.value?.bar_manifest?.find((item) => item.symbol === selectedSymbol.value)?.source ?? 'daily_bars')
const firstWarning = computed(() => quality.value?.warnings?.[0] ?? dataset.value?.quality?.warnings?.[0] ?? '')
const latestIndicatorSnapshot = computed(() => projection.value?.instruments?.[selectedSymbol.value]?.indicator_snapshot_id ?? null)
const symbolStrategyDecisions = computed(() => strategyDecisions.value.filter((item) => item.scope.symbol === selectedSymbol.value))
const activeStrategyDecision = computed(() => symbolStrategyDecisions.value.find((item) => item.strategy.name === activeStrategy.value) ?? null)
const synthesisLabel = computed(() => {
  const labels: Record<string, string> = {
    aligned: '方向一致',
    mixed: '信号混合',
    conflicted: '策略冲突',
    no_signal: '暂无信号',
    insufficient_data: '数据不足',
  }
  return labels[deterministicSynthesis.value.consensus_state ?? ''] ?? '等待策略'
})
const synthesisTone = computed(() => {
  const state = deterministicSynthesis.value.consensus_state
  if (state === 'aligned') return 'positive'
  if (state === 'conflicted' || state === 'insufficient_data') return 'negative'
  if (state === 'mixed') return 'warning'
  return 'neutral'
})
const synthesisActionLabel = computed(() => {
  const labels: Record<string, string> = { prioritize: '优先观察', watch: '等待确认', avoid: '回避', wait: '等待', no_action: '不行动' }
  return labels[deterministicSynthesis.value.suggested_action ?? ''] ?? '—'
})
const strategyNameLabels: Record<string, string> = {
  trend_momentum_v1: '趋势动量',
  mean_reversion_v1: '均值回归',
  breakout_volume_v1: '突破量能',
  relative_strength_rotation_v1: '相对强弱轮动',
  quality_left_side_reversal_v1: '优质资产左侧反转',
}
const strategyActionLabels: Record<string, string> = {
  prioritize: '优先观察',
  watch: '观察',
  wait: '等待',
  avoid: '回避',
  no_action: '不行动',
}
const strategyStanceLabels: Record<string, string> = { bullish: '偏多', bearish: '偏空', neutral: '中性', insufficient_data: '不可用' }
const historicalDatasetId = computed(() => typeof route.query.dataset === 'string' ? route.query.dataset : '')

function formatPrice(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return value.toFixed(2)
}

function formatPercent(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
}

function formatAtrDistance(value: number | null | undefined) {
  return value === null || value === undefined || !Number.isFinite(value) ? '—' : `${value.toFixed(2)} ATR`
}

function formatHash(value: string | null | undefined) {
  return value ? `${value.slice(0, 12)}…${value.slice(-6)}` : '—'
}

function setLayer(key: LayerKey) {
  layers[key] = !layers[key]
}

function setRange(value: string) {
  if (value === '3M' || value === '6M' || value === '1Y' || value === 'ALL') range.value = value
}

function toggleWatchGroup(groupId: string) {
  expandedGroups[groupId] = !expandedGroups[groupId]
}

function chooseSymbol(symbol: string) {
  const normalized = symbol.trim().toUpperCase()
  if (!normalized || normalized === selectedSymbol.value) return
  const group = watchGroups.value.find((item) => item.symbols.includes(normalized))
  if (group) expandedGroups[group.id] = true
  selectedSymbol.value = normalized
  symbolDraft.value = normalized
  cursorIndex.value = null
  activeStrategy.value = null
  router.replace({ name: 'instrument-decision', params: { symbol: normalized } }).catch(() => undefined)
  void loadEvidence()
}

function submitSymbol() {
  chooseSymbol(symbolDraft.value)
}

async function loadEvidence() {
  loading.value = true
  error.value = ''
  demoReason.value = ''
  const datasetId = historicalDatasetId.value
  try {
    if (datasetId) {
      const [storedDataset, storedChart, storedStrategies] = await Promise.all([
        api.getDailyDataset(datasetId),
        api.getDailyChart(datasetId),
        api.getDailyStrategies(datasetId),
      ])
      if (storedDataset.dataset_id !== datasetId || storedChart.dataset_id !== datasetId || storedStrategies.dataset_id !== datasetId) {
        throw new Error('冻结证据的 dataset_id 不一致，已拒绝加载。')
      }
      const chartBars = storedChart.instruments?.[selectedSymbol.value]?.price?.bars ?? []
      if (!chartBars.length) throw new Error(`冻结 dataset 中没有 ${selectedSymbol.value} 的日 K`)
      applyResponse({
        dataset: storedDataset,
        chart: storedChart,
        strategy_decisions: storedStrategies.strategy_decisions,
        deterministic_synthesis: storedStrategies.deterministic_synthesis,
      })
    } else {
      const response = await api.createDailyDataset({
        scope_type: 'instrument',
        scope_id: selectedSymbol.value,
        symbols: [selectedSymbol.value],
        benchmark_symbols: ['QQQ'],
        scope_version: 1,
      })
      const chartBars = response.chart?.instruments?.[selectedSymbol.value]?.price?.bars ?? []
      if (!chartBars.length) throw new Error('后端尚未返回可绘制的完整日 K')
      applyResponse(response)
    }
  } catch (reason) {
    const message = reason instanceof Error ? reason.message : 'Daily Evidence 请求失败。'
    if (datasetId) {
      error.value = `冻结证据加载失败：${message}`
      dataset.value = null
      projection.value = null
      strategyDecisions.value = []
      deterministicSynthesis.value = {}
    } else {
      demoReason.value = `${message} · 当前显示本地演示数据，明确标记为 LOCAL DEMO。`
      applyResponse(buildDemoResponse(selectedSymbol.value))
    }
  } finally {
    loading.value = false
  }
}

function applyResponse(response: DailyEvidenceResponse) {
  dataset.value = response.dataset
  projection.value = response.chart
  strategyDecisions.value = response.strategy_decisions ?? []
  deterministicSynthesis.value = response.deterministic_synthesis ?? {}
}

function selectStrategy(strategyName: string) {
  activeStrategy.value = activeStrategy.value === strategyName ? null : strategyName
}

function showAiPlaceholder() {
  aiNotice.value = 'Phase B 确定性策略已完成；AI 仲裁仍由后续 Anomalo Workflow 接入，当前不会在 Urus 本地伪造结果。'
}

function closeAiNotice() {
  aiNotice.value = ''
}

function movingAverage(values: number[], window: number): Array<number | null> {
  return values.map((_, index) => index + 1 >= window ? values.slice(index - window + 1, index + 1).reduce((sum, item) => sum + item, 0) / window : null)
}

function makeSeries(seriesId: string, pane: ChartSeries['pane'], kind: ChartSeries['kind'], dates: string[], values: Array<number | null>, unit = 'index', bounds?: ChartSeries['bounds']): ChartSeries {
  return { series_id: seriesId, pane, kind, unit, bounds, points: dates.map((time, index) => ({ time, value: values[index] === null ? null : Number(values[index]?.toFixed(6)) })) }
}

function buildDemoResponse(symbol: string): DailyEvidenceResponse {
  const dates: string[] = []
  const cursor = new Date('2026-08-21T00:00:00Z')
  while (dates.length < 220) {
    const weekday = cursor.getUTCDay()
    if (weekday !== 0 && weekday !== 6) dates.unshift(cursor.toISOString().slice(0, 10))
    cursor.setUTCDate(cursor.getUTCDate() - 1)
  }
  const base = symbol === 'NVDA' ? 172 : symbol === 'LITE' ? 58 : symbol === 'COHR' ? 112 : symbol === 'AMD' ? 136 : 31
  const closes = dates.map((_, index) => base + index * 0.055 + Math.sin(index / 8) * 1.4 + Math.sin(index / 21) * 2.2)
  const bars: DailyBar[] = closes.map((close, index) => ({
    date: dates[index],
    open: close - 0.42 - Math.sin(index) * 0.08,
    high: close + 0.72 + Math.abs(Math.sin(index / 3)) * 0.28,
    low: close - 0.8 - Math.abs(Math.cos(index / 4)) * 0.2,
    close,
    volume: 1_100_000 + (Math.sin(index / 6) + 1) * 360_000 + (index % 17 === 0 ? 1_500_000 : 0),
    adjustment: 'QFQ',
  }))
  const volumes = bars.map((bar) => bar.volume)
  const ma20Values = movingAverage(closes, 20)
  const ma50Values = movingAverage(closes, 50)
  const ma200Values = movingAverage(closes, 200)
  const bbUpper: Array<number | null> = []
  const bbMiddle: Array<number | null> = []
  const bbLower: Array<number | null> = []
  const bbWidth: Array<number | null> = []
  const rsiValues: Array<number | null> = []
  const macdDif: Array<number | null> = []
  const macdDea: Array<number | null> = []
  const macdHistogram: Array<number | null> = []
  for (let index = 0; index < closes.length; index += 1) {
    if (index < 19) {
      bbUpper.push(null); bbMiddle.push(null); bbLower.push(null); bbWidth.push(null)
    } else {
      const window = closes.slice(index - 19, index + 1)
      const mean = window.reduce((sum, value) => sum + value, 0) / window.length
      const deviation = Math.sqrt(window.reduce((sum, value) => sum + (value - mean) ** 2, 0) / window.length)
      bbMiddle.push(mean); bbUpper.push(mean + deviation * 2); bbLower.push(mean - deviation * 2); bbWidth.push((deviation * 4) / mean * 100)
    }
    const change = index === 0 ? 0 : closes[index] - closes[index - 1]
    const lookback = Math.max(1, Math.min(14, index))
    const gains = closes.slice(index - lookback, index + 1).map((value, offset, values) => offset ? Math.max(0, value - values[offset - 1]) : 0)
    const losses = closes.slice(index - lookback, index + 1).map((value, offset, values) => offset ? Math.max(0, values[offset - 1] - value) : 0)
    const gain = gains.reduce((sum, value) => sum + value, 0) / lookback
    const loss = losses.reduce((sum, value) => sum + value, 0) / lookback
    rsiValues.push(index < 14 ? null : loss === 0 ? 70 + Math.min(28, change * 4) : 100 - 100 / (1 + gain / loss))
    const fast = index < 11 ? null : closes.slice(index - 11, index + 1).reduce((sum, value) => sum + value, 0) / 12
    const slow = index < 25 ? null : closes.slice(index - 25, index + 1).reduce((sum, value) => sum + value, 0) / 26
    const dif = fast !== null && slow !== null ? fast - slow : null
    macdDif.push(dif)
    macdDea.push(dif === null ? null : index < 33 ? dif : (macdDif.slice(Math.max(0, index - 8), index + 1).filter((value): value is number => value !== null).reduce((sum, value) => sum + value, 0) / 9))
    const dea = macdDea[index]
    macdHistogram.push(dif !== null && dea !== null ? dif - dea : null)
  }
  const volumeMa20 = movingAverage(volumes, 20)
  const relativeValues = closes.map((_, index) => 100 + Math.sin(index / 13) * 2.5 + index * 0.012)
  const chartSeries: ChartSeries[] = [
    makeSeries('close', 'price', 'line', dates, closes, 'price'),
    makeSeries('ma20', 'price', 'line', dates, ma20Values, 'price'),
    makeSeries('ma50', 'price', 'line', dates, ma50Values, 'price'),
    makeSeries('ma200', 'price', 'line', dates, ma200Values, 'price'),
    makeSeries('volume', 'volume', 'bar', dates, volumes, 'shares'),
    makeSeries('volume_ma20', 'volume', 'line', dates, volumeMa20, 'shares'),
    makeSeries('bollinger_upper_20_2', 'price', 'line', dates, bbUpper, 'price'),
    makeSeries('bollinger_middle_20', 'price', 'line', dates, bbMiddle, 'price'),
    makeSeries('bollinger_lower_20_2', 'price', 'line', dates, bbLower, 'price'),
    makeSeries('bollinger_bandwidth_20', 'volatility', 'line', dates, bbWidth, 'percent'),
    makeSeries('rsi14', 'momentum', 'line', dates, rsiValues, 'index', { min: 0, max: 100, reference_lines: [30, 50, 70] }),
    makeSeries('macd_dif_12_26', 'momentum', 'line', dates, macdDif, 'price'),
    makeSeries('macd_dea_9', 'momentum', 'line', dates, macdDea, 'price'),
    makeSeries('macd_histogram', 'momentum', 'histogram', dates, macdHistogram, 'price'),
    makeSeries('relative_performance_vs_QQQ', 'relative_strength', 'line', dates, relativeValues, 'index'),
  ]
  const quality = {
    status: 'partial',
    symbols: {
      [symbol]: {
        status: 'partial',
        bar_count: bars.length,
        latest_bar_date: dates[dates.length - 1],
        input_bar_hash: 'local-demo-input',
        warnings: ['这是前端本地演示数据，不代表市场事实。'],
        is_benchmark: false,
      },
    },
    requested_symbol_count: 1,
    available_symbol_count: 1,
    errors: [],
    warnings: ['LOCAL DEMO：后端没有返回可绘制的 Daily Evidence。'],
  }
  const scope = { scope_type: 'instrument' as const, scope_id: symbol, scope_version: 1, symbols: [symbol], benchmark_symbols: ['QQQ'], trading_date: dates[dates.length - 1] }
  const demoDataset: DailyDecisionDataset = {
    schema_version: 'urus.daily_decision_dataset.local-demo.v1',
    feature_version: 'technical_v5',
    dataset_id: 'local-demo-dataset',
    trading_date: dates[dates.length - 1],
    cutoff_time: '2026-08-22T05:30:00Z',
    market_timezone: 'America/New_York',
    bar_completion_policy: 'official_exchange_close_only_v1',
    scope,
    bar_manifest: [{ symbol, bar_count: bars.length, start_date: dates[0], end_date: dates[dates.length - 1], input_bar_hash: 'local-demo-input', source: 'local_demo', adjustment: 'QFQ', exchange: 'XNYS', source_revisions: ['local-demo'], quality_status: 'partial' }],
    indicator_snapshot_ids: ['local-demo-indicator'],
    group_snapshot_ids: [],
    quality,
    status: 'partial',
    content_sha256: 'local-demo-content',
  }
  const instrument = { symbol, price: { symbol, price_format: { precision: 2, currency: 'USD' }, bars }, series: chartSeries, indicator_snapshot_id: 'local-demo-indicator', quality: quality.symbols[symbol] }
  const demoChart: DecisionChartProjection = {
    schema_version: 'urus.decision_chart_projection.local-demo.v1',
    dataset_id: demoDataset.dataset_id,
    scope,
    timezone: 'America/New_York',
    instruments: { [symbol]: instrument },
    price: instrument.price,
    series: chartSeries,
    indicator_snapshot_id: instrument.indicator_snapshot_id,
    overlays: [],
    state_segments: [],
    events: [],
    quality,
    content_sha256: 'local-demo-chart',
  }
  return { dataset: demoDataset, chart: demoChart, strategy_decisions: [], deterministic_synthesis: {} }
}

async function loadWatchGroups() {
  try {
    const groups = await api.listObservationGroups()
    watchGroups.value = groups
      .map((group) => ({
        id: group.group_id,
        name: group.display_name,
        benchmark: group.benchmark_symbols[0] ?? '—',
        symbols: group.symbols,
        source: group.source ?? 'manual',
        tags: group.tags ?? [],
      }))
      .filter((group) => !isLegacySelfSelectedGroup(group))
    for (const group of watchGroups.value) {
      if (expandedGroups[group.id] === undefined) expandedGroups[group.id] = !isIndicatorGroup(group)
    }
  } catch {
    // Keep local UI debugging usable when the observation API is offline.
    watchGroups.value = defaultWatchGroups.map((group) => ({ ...group, symbols: [...group.symbols], tags: [...group.tags] }))
    for (const group of watchGroups.value) expandedGroups[group.id] = !isIndicatorGroup(group)
  }
}

onMounted(() => {
  void loadWatchGroups()
  void loadEvidence()
})
</script>

<template>
  <div class="decision-app">
    <AppShell />
    <div class="decision-layout">
      <aside class="decision-scope-rail">
        <div class="decision-scope-inner">
        <div class="rail-topline"><span class="rail-pulse"></span> EVIDENCE DESK</div>
        <div class="scope-block">
          <span class="rail-label">DECISION PATH</span>
          <div class="scope-path" aria-label="大盘、板块、个股三级决策路径">
            <div class="scope-level">
              <span class="scope-level-index">01</span>
              <span class="scope-level-copy"><small>MARKET</small><strong>美股大盘</strong><em>SPY · QQQ</em></span>
            </div>
            <div class="scope-level ancestor">
              <span class="scope-level-index">02</span>
              <span class="scope-level-copy"><small>SECTOR</small><strong>{{ selectedGroup.name }}</strong><em>{{ selectedGroup.benchmark }} · {{ selectedGroup.symbols.length }} stocks</em></span>
            </div>
            <div class="scope-level current">
              <span class="scope-level-index">03</span>
              <span class="scope-level-copy"><small>INSTRUMENT</small><strong>{{ selectedSymbol }}</strong><em>日 K · Phase A</em></span>
            </div>
          </div>
        </div>
        <div class="rail-divider"></div>
        <div class="watchlist-rail-scroll">
          <div v-if="indicatorGroups.length" class="watchlist-block watchlist-core-block">
            <div class="rail-section-title"><span class="rail-label">指标推荐</span><span class="rail-count">{{ indicatorGroups.length }} / {{ indicatorSymbolCount }}</span></div>
            <div class="watch-groups">
              <section v-for="group in indicatorGroups" :key="group.id" class="watch-group">
                <button class="watch-group-toggle" :class="{ active: group.id === selectedGroup.id }" type="button" :aria-expanded="expandedGroups[group.id]" @click="toggleWatchGroup(group.id)">
                  <span><strong>{{ groupDisplayName(group) }}</strong><small>{{ groupSubtitle(group) }}</small></span>
                  <span class="watch-group-meta"><b>{{ group.symbols.length }}</b><i>{{ expandedGroups[group.id] ? '−' : '+' }}</i></span>
                </button>
                <div v-show="expandedGroups[group.id]" class="watch-symbol-list">
                  <button v-for="symbol in group.symbols" :key="symbol" class="watch-symbol" :class="{ active: symbol === selectedSymbol }" type="button" @click="chooseSymbol(symbol)">
                    <span class="watch-symbol-dot"></span><strong>{{ symbol }}</strong><small>{{ symbol === selectedSymbol ? 'OPEN' : 'VIEW' }}</small>
                  </button>
                </div>
              </section>
            </div>
          </div>

          <div class="watchlist-block">
            <div class="rail-section-title"><span class="rail-label">SECTOR WATCHLIST</span><span class="rail-count">{{ sectorGroups.length }} / {{ sectorSymbolCount }}</span></div>
            <div class="watch-groups">
              <section v-for="group in sectorGroups" :key="group.id" class="watch-group">
                <button class="watch-group-toggle" :class="{ active: group.id === selectedGroup.id }" type="button" :aria-expanded="expandedGroups[group.id]" @click="toggleWatchGroup(group.id)">
                  <span><strong>{{ groupDisplayName(group) }}</strong><small>{{ groupSubtitle(group) }}</small></span>
                  <span class="watch-group-meta"><b>{{ group.symbols.length }}</b><i>{{ expandedGroups[group.id] ? '−' : '+' }}</i></span>
                </button>
                <div v-show="expandedGroups[group.id]" class="watch-symbol-list">
                  <button v-for="symbol in group.symbols" :key="symbol" class="watch-symbol" :class="{ active: symbol === selectedSymbol }" type="button" @click="chooseSymbol(symbol)">
                    <span class="watch-symbol-dot"></span><strong>{{ symbol }}</strong><small>{{ symbol === selectedSymbol ? 'OPEN' : 'VIEW' }}</small>
                  </button>
                </div>
              </section>
            </div>
          </div>
        </div>
        <div class="rail-footer">
          <span class="phase-chip">PHASE A</span>
          <p>只读证据<br />不生成订单</p>
        </div>
        </div>
      </aside>

      <main class="decision-main">
        <section class="decision-topline">
          <div class="decision-heading">
            <div class="breadcrumb"><span>美股大盘</span><b>/</b><span>{{ selectedGroup.name }}</span><b>/</b><span>{{ selectedSymbol }}</span></div>
            <div class="title-row"><h1>{{ selectedSymbol }}</h1><span class="instrument-context">{{ selectedGroup.name }} · {{ selectedGroup.benchmark }}</span></div>
          </div>
          <div class="decision-actions">
            <div class="connection-state" :data-demo="demoReason ? 'demo' : 'api'"><i></i><span>{{ demoReason ? 'LOCAL DEMO' : 'EVIDENCE API' }}</span></div>
            <button class="ai-action" type="button" @click="showAiPlaceholder">AI 评估 <small>未接入</small></button>
          </div>
        </section>

        <div v-if="demoReason" class="demo-banner"><span>LOCAL DEMO</span>{{ demoReason }}</div>
        <div v-if="error" class="decision-error">{{ error }}</div>

        <section class="symbol-command-bar">
          <form class="symbol-search" @submit.prevent="submitSymbol">
            <span class="search-icon">⌕</span>
            <input v-model="symbolDraft" aria-label="输入股票代码" placeholder="输入 symbol" />
            <button type="submit">打开</button>
          </form>
          <div class="date-readout"><span>TRADING DATE</span><strong>{{ dataset?.trading_date ?? '—' }}</strong><small>{{ dataset?.market_timezone ?? 'America/New_York' }}</small></div>
          <div class="data-readout"><span>DATASET</span><strong>{{ dataset?.status === 'ok' ? 'FROZEN' : dataset?.status?.toUpperCase() ?? 'WAITING' }}</strong><small>{{ formatHash(dataset?.content_sha256) }}</small></div>
          <div class="latest-readout"><span>LAST CLOSE</span><strong>{{ formatPrice(latestBar?.close) }}</strong><small :class="latestChange !== null && latestChange >= 0 ? 'positive-text' : 'negative-text'">{{ formatPercent(latestChange) }}</small></div>
        </section>

        <section class="chart-toolbar">
          <div class="range-tabs"><span class="toolbar-label">WINDOW</span><button v-for="item in ['3M', '6M', '1Y', 'ALL']" :key="item" type="button" :class="{ active: range === item }" @click="setRange(item)">{{ item }}</button></div>
          <div class="layer-tabs"><span class="toolbar-label">LAYERS</span><button v-for="layer in layerLabels" :key="layer.key" type="button" :class="{ active: layers[layer.key] }" :aria-label="layer.label" @click="setLayer(layer.key)"><i :data-layer="layer.key"></i>{{ layer.short }}</button></div>
          <span v-if="loading" class="loading-label"><i></i> FREEZING EVIDENCE</span>
        </section>

        <section class="decision-content-grid">
          <DecisionChartWorkspace v-model:cursor-index="cursorIndex" :projection="projection" :symbol="selectedSymbol" :range="range" :layers="layers" :strategy-filter="activeStrategy" />

          <aside class="decision-insight-rail">
            <article class="insight-card read-card">
              <div class="insight-card-head"><div><span class="section-kicker">READ THE CHART</span><h2>当前事实</h2></div><span class="fact-lock">LOCKED</span></div>
              <div class="fact-stack">
                <div class="fact-row"><span class="fact-icon" :data-tone="trend.tone">↗</span><div><small>TREND REGIME</small><strong>{{ trend.label }}</strong><p>{{ trend.detail }}</p></div></div>
                <div class="fact-row"><span class="fact-icon" :data-tone="momentum.tone">◒</span><div><small>MOMENTUM</small><strong>{{ momentum.label }}</strong><p>{{ momentum.detail }}</p></div></div>
                <div class="fact-row"><span class="fact-icon" :data-tone="relativeState.tone">⇄</span><div><small>RELATIVE STRENGTH</small><strong>{{ relativeState.label }}</strong><p>{{ relativeState.detail }}</p></div></div>
              </div>
            </article>

            <article class="insight-card handoff-card">
              <div class="insight-card-head"><div><span class="section-kicker">STRATEGY LAYER</span><h2>算法建议</h2></div><span class="phase-chip small">{{ symbolStrategyDecisions.length }} STRATEGIES</span></div>
              <div class="synthesis-panel" :data-tone="synthesisTone">
                <div><span>DETERMINISTIC SYNTHESIS</span><strong>{{ synthesisLabel }}</strong></div>
                <b>{{ synthesisActionLabel }}</b>
              </div>
              <p class="synthesis-summary">{{ deterministicSynthesis.conflict_summary || '策略会读取同一份冻结日 K，独立输出方向、风险和确认条件。' }}</p>
              <div v-if="symbolStrategyDecisions.length" class="strategy-decision-list">
                <div v-for="decision in symbolStrategyDecisions" :key="decision.decision_id" class="strategy-decision-row" :class="{ active: activeStrategy === decision.strategy.name }" :data-status="decision.status" :data-tone="decision.stance === 'bullish' ? 'positive' : decision.stance === 'bearish' ? 'negative' : 'neutral'" role="button" tabindex="0" @click="selectStrategy(decision.strategy.name)" @keydown.enter.prevent="selectStrategy(decision.strategy.name)" @keydown.space.prevent="selectStrategy(decision.strategy.name)">
                  <div class="strategy-decision-head"><span><i></i><strong>{{ strategyNameLabels[decision.strategy.name] ?? decision.strategy.name }}</strong></span><b>{{ strategyActionLabels[decision.action] ?? decision.action }}</b></div>
                  <p>{{ decision.reasons[0]?.detail ?? '当前没有额外解释。' }}</p>
                  <small>{{ strategyStanceLabels[decision.stance] ?? decision.stance }} · score {{ decision.score ?? '—' }} · {{ decision.setup_progress.stage }}</small>
                </div>
              </div>
              <div v-if="activeStrategyDecision" class="strategy-detail">
                <div class="strategy-detail-head"><span>SELECTED STRATEGY</span><strong>{{ strategyNameLabels[activeStrategyDecision.strategy.name] ?? activeStrategyDecision.strategy.name }}</strong></div>
                <div class="strategy-detail-progress"><b>{{ activeStrategyDecision.setup_progress.stage }}</b><span>确认距 {{ formatAtrDistance(activeStrategyDecision.setup_progress.confirmation_distance_atr) }}</span><span>失效距 {{ formatAtrDistance(activeStrategyDecision.setup_progress.invalidation_distance_atr) }}</span></div>
                <p>{{ activeStrategyDecision.risks[0] ?? '当前没有额外风险说明。' }}</p>
                <p><em>确认</em>{{ activeStrategyDecision.confirmation_conditions[0] ?? '暂无确认条件。' }}</p>
                <p><em>失效</em>{{ activeStrategyDecision.invalidation_conditions[0] ?? '暂无失效条件。' }}</p>
                <small>HORIZON {{ activeStrategyDecision.horizon.value }} {{ activeStrategyDecision.horizon.unit }} · {{ activeStrategyDecision.evidence_refs.length }} EVIDENCE REFS</small>
              </div>
              <div v-if="!symbolStrategyDecisions.length" class="strategy-placeholder"><span class="placeholder-mark">—</span><div><strong>策略暂不可用</strong><small>LOCAL DEMO 或数据尚未通过策略质量 Gate</small></div></div>
            </article>

            <article class="insight-card ai-card">
              <div class="insight-card-head"><div><span class="section-kicker">AI ARBITRATION</span><h2>AI 决策</h2></div><span class="not-run">NOT RUN</span></div>
              <p>AI 只能在用户主动发起后评估这份冻结证据。执行能力将通过 Anomalo Workflow 接入。</p>
              <button class="ai-placeholder-button" type="button" @click="showAiPlaceholder">主动发起 AI 评估 <span>→</span></button>
              <div v-if="aiNotice" class="ai-notice"><button type="button" aria-label="关闭提示" @click="closeAiNotice">×</button>{{ aiNotice }}</div>
            </article>
          </aside>
        </section>

        <section class="quality-strip">
          <div class="quality-title"><span class="section-kicker">DATA QUALITY GATE</span><strong :data-tone="qualityState.tone"><i></i>{{ qualityState.label }}</strong></div>
          <div class="quality-stat"><span>BARS</span><strong>{{ quality?.bar_count ?? 0 }}</strong><small>要求 ≥ 260</small></div>
          <div class="quality-stat"><span>AS OF</span><strong>{{ quality?.latest_bar_date ?? '—' }}</strong><small>完整收市 K</small></div>
          <div class="quality-stat"><span>SOURCE</span><strong>{{ sourceLabel }}</strong><small>{{ dataset?.bar_manifest?.find((item) => item.symbol === selectedSymbol)?.adjustment ?? 'QFQ' }}</small></div>
          <div class="quality-warning" :data-tone="qualityState.tone"><span>!</span><p>{{ firstWarning || '交易日连续 · OHLCV 合法 · 无质量警告' }}</p></div>
        </section>

        <section class="evidence-footer-grid">
          <article class="evidence-card evidence-version-card">
            <div class="evidence-card-head">
              <div><span class="section-kicker">EVIDENCE VERSION</span><h2>本次分析证据</h2><p>锁定行情窗口、指标版本和收市规则，保证后续策略与 AI 使用同一份输入。</p></div>
              <span class="evidence-state" :data-tone="qualityState.tone"><i></i>{{ dataset?.status === 'ok' ? 'FROZEN' : dataset?.status?.toUpperCase() ?? 'WAITING' }}</span>
            </div>
            <div class="evidence-identity">
              <div><span>DATASET ID</span><strong>{{ dataset?.dataset_id ?? '—' }}</strong></div>
              <span class="hash-label">SHA-256 {{ formatHash(dataset?.content_sha256) }}</span>
            </div>
            <div class="manifest-grid">
              <div><span>BAR WINDOW</span><strong>{{ dataset?.bar_manifest?.find((item) => item.symbol === selectedSymbol)?.start_date ?? '—' }} → {{ dataset?.bar_manifest?.find((item) => item.symbol === selectedSymbol)?.end_date ?? '—' }}</strong></div>
              <div><span>FEATURE VERSION</span><strong>technical_v5</strong></div>
              <div><span>INDICATOR SNAPSHOT</span><strong>{{ formatHash(latestIndicatorSnapshot) }}</strong></div>
              <div><span>COMPLETION POLICY</span><strong>official close only</strong></div>
            </div>
            <button class="detail-toggle" type="button" @click="showTechnicalDetails = !showTechnicalDetails">{{ showTechnicalDetails ? '收起技术明细' : '展开技术明细' }} <span>{{ showTechnicalDetails ? '↑' : '↓' }}</span></button>
            <div v-if="showTechnicalDetails" class="technical-detail-grid">
              <div><span>MA20</span><strong>{{ formatPrice(ma20) }}</strong></div><div><span>MA50</span><strong>{{ formatPrice(ma50) }}</strong></div><div><span>MA200</span><strong>{{ formatPrice(ma200) }}</strong></div><div><span>RSI14</span><strong>{{ rsi === null ? '—' : rsi.toFixed(1) }}</strong></div><div><span>MACD HIST</span><strong>{{ macdHistogram === null ? '—' : macdHistogram.toFixed(3) }}</strong></div><div><span>RELATIVE</span><strong>{{ relative === null ? '—' : relative.toFixed(1) }}</strong></div>
            </div>
          </article>
          <article class="reading-guide">
            <div class="reading-guide-head"><div><span class="section-kicker">DECISION CHECKLIST</span><h2>决策检查清单</h2></div><span>3 STEPS</span></div>
            <p class="reading-guide-intro">把图表事实整理成进入策略层前的三个检查点。</p>
            <div class="decision-checklist">
              <div><span class="checklist-index">01</span><div><strong>趋势结构</strong><p>价格与 MA20 / MA50 的关系</p></div><em>{{ trend.label }}</em></div>
              <div><span class="checklist-index">02</span><div><strong>动量状态</strong><p>RSI 与 MACD 是否形成确认</p></div><em>{{ momentum.label }}</em></div>
              <div><span class="checklist-index">03</span><div><strong>策略综合</strong><p>全部策略方向与冲突状态</p></div><em>{{ synthesisLabel }}</em></div>
            </div>
            <RouterLink class="text-link" to="/research">查看历史研究库 →</RouterLink>
          </article>
        </section>
      </main>
    </div>
  </div>
</template>
