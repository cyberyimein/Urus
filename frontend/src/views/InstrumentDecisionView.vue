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
} from '@/types/dailyEvidence'

type LayerKey = 'ma20' | 'ma50' | 'ma200' | 'bollinger' | 'volume' | 'rsi' | 'macd' | 'relative'

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

const watchlist = ['INTC', 'NVDA', 'LITE', 'COHR', 'AMD']
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

function formatPrice(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return value.toFixed(2)
}

function formatPercent(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
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

function chooseSymbol(symbol: string) {
  const normalized = symbol.trim().toUpperCase()
  if (!normalized || normalized === selectedSymbol.value) return
  selectedSymbol.value = normalized
  symbolDraft.value = normalized
  cursorIndex.value = null
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
  try {
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
  } catch (reason) {
    const message = reason instanceof Error ? reason.message : 'Daily Evidence 请求失败。'
    demoReason.value = `${message} · 当前显示本地演示数据，明确标记为 LOCAL DEMO。`
    applyResponse(buildDemoResponse(selectedSymbol.value))
  } finally {
    loading.value = false
  }
}

function applyResponse(response: DailyEvidenceResponse) {
  dataset.value = response.dataset
  projection.value = response.chart
}

function showAiPlaceholder() {
  aiNotice.value = '当前只完成 Phase A 证据冻结；AI 执行会通过 Anomalo Workflow 接口接入，不在 Urus 本地伪造结果。'
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
  return { dataset: demoDataset, chart: demoChart }
}

onMounted(() => {
  void loadEvidence()
})
</script>

<template>
  <div class="decision-app">
    <AppShell />
    <div class="decision-layout">
      <aside class="decision-scope-rail">
        <div class="rail-topline"><span class="rail-pulse"></span> EVIDENCE DESK</div>
        <div class="scope-block">
          <span class="rail-label">DECISION SCOPE</span>
          <button class="scope-button active" type="button"><strong>个股</strong><small>instrument / Phase A</small></button>
          <button class="scope-button disabled" type="button" disabled><strong>观察组</strong><small>group / Phase C</small></button>
          <button class="scope-button disabled" type="button" disabled><strong>收市运行</strong><small>observation / Phase C</small></button>
        </div>
        <div class="rail-divider"></div>
        <div class="scope-block">
          <div class="rail-section-title"><span class="rail-label">WATCHLIST</span><span class="rail-count">{{ watchlist.length }}</span></div>
          <button v-for="symbol in watchlist" :key="symbol" class="watch-symbol" :class="{ active: symbol === selectedSymbol }" type="button" @click="chooseSymbol(symbol)">
            <span class="watch-symbol-dot"></span><strong>{{ symbol }}</strong><small>{{ symbol === selectedSymbol ? 'OPEN' : 'VIEW' }}</small>
          </button>
        </div>
        <div class="rail-footer">
          <span class="phase-chip">PHASE A</span>
          <p>只读证据<br />不生成订单</p>
        </div>
      </aside>

      <main class="decision-main">
        <section class="decision-topline">
          <div class="decision-heading">
            <div class="breadcrumb"><span>DAILY EVIDENCE</span><b>/</b><span>INSTRUMENT</span></div>
            <div class="title-row"><h1>{{ selectedSymbol }}</h1><span class="title-suffix">日 K 决策工作台</span></div>
            <p class="subtitle">先看完整事实，再看算法策略，最后由用户主动发起 AI 评估。</p>
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
          <DecisionChartWorkspace v-model:cursor-index="cursorIndex" :projection="projection" :symbol="selectedSymbol" :range="range" :layers="layers" />

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
              <div class="insight-card-head"><div><span class="section-kicker">STRATEGY LAYER</span><h2>算法建议</h2></div><span class="phase-chip small">PHASE B</span></div>
              <p class="handoff-copy">Phase A 只负责把完整日 K 和技术事实冻结好。策略 Registry 接入后，RSI、趋势、突破等算法会在这里分别给出建议。</p>
              <div class="strategy-placeholder"><span class="placeholder-mark">+</span><div><strong>等待 Strategy Registry</strong><small>不会把 RSI 数字伪装成买卖信号</small></div></div>
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
          <article class="evidence-card">
            <div class="evidence-card-head"><div><span class="section-kicker">EVIDENCE MANIFEST</span><h2>这张图从哪里来</h2></div><span class="hash-label">SHA-256 {{ formatHash(dataset?.content_sha256) }}</span></div>
            <div class="manifest-grid">
              <div><span>BAR WINDOW</span><strong>{{ dataset?.bar_manifest?.find((item) => item.symbol === selectedSymbol)?.start_date ?? '—' }} → {{ dataset?.bar_manifest?.find((item) => item.symbol === selectedSymbol)?.end_date ?? '—' }}</strong></div>
              <div><span>FEATURE VERSION</span><strong>technical_v4</strong></div>
              <div><span>INDICATOR SNAPSHOT</span><strong>{{ formatHash(latestIndicatorSnapshot) }}</strong></div>
              <div><span>COMPLETION POLICY</span><strong>official close only</strong></div>
            </div>
            <button class="detail-toggle" type="button" @click="showTechnicalDetails = !showTechnicalDetails">{{ showTechnicalDetails ? '收起技术明细' : '展开技术明细' }} <span>{{ showTechnicalDetails ? '↑' : '↓' }}</span></button>
            <div v-if="showTechnicalDetails" class="technical-detail-grid">
              <div><span>MA20</span><strong>{{ formatPrice(ma20) }}</strong></div><div><span>MA50</span><strong>{{ formatPrice(ma50) }}</strong></div><div><span>MA200</span><strong>{{ formatPrice(ma200) }}</strong></div><div><span>RSI14</span><strong>{{ rsi === null ? '—' : rsi.toFixed(1) }}</strong></div><div><span>MACD HIST</span><strong>{{ macdHistogram === null ? '—' : macdHistogram.toFixed(3) }}</strong></div><div><span>RELATIVE</span><strong>{{ relative === null ? '—' : relative.toFixed(1) }}</strong></div>
            </div>
          </article>
          <article class="reading-guide">
            <span class="section-kicker">HUMAN READING ORDER</span>
            <h2>先找变化，再看数字</h2>
            <ol><li><b>趋势</b><span>价格与 MA20 / MA50 的位置</span></li><li><b>动量</b><span>RSI 与 MACD 是否同步</span></li><li><b>确认</b><span>等待策略层给出确认/失效位</span></li></ol>
            <RouterLink class="text-link" to="/research">查看历史研究库 →</RouterLink>
          </article>
        </section>
      </main>
    </div>
  </div>
</template>
