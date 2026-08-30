<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { api } from '@/api/client'
import PostCloseOptionAlignment from '@/components/PostCloseOptionAlignment.vue'
import type {
  PostCloseOptionAlignment as PostCloseOptionAlignmentData,
  ReportDisplayManifest,
  ReportDisplayOptionPayload,
  TechnicalReport,
  TechnicalSection,
} from '@/types/research'
import { formatDate, formatNumber } from '@/utils/format'

const props = defineProps<{
  report: TechnicalReport | null
  activeSection?: TechnicalSection
  reportId?: string
}>()
const emit = defineEmits<{ (event: 'select-section', value: TechnicalSection): void }>()

const selectedSection = computed<TechnicalSection>(() => props.activeSection ?? 'overview')
const secondaryTabs: Array<{ id: TechnicalSection; label: string; hint: string }> = [
  { id: 'overview', label: '总览', hint: '质量 · 市场' },
  { id: 'instruments', label: '个股技术', hint: '题材矩阵' },
  { id: 'options', label: '期权结构', hint: 'DEX · GEX · Gamma' },
  { id: 'events', label: '事件时间轴', hint: '日历 · 结果' },
]

type Dict = Record<string, any>

const themeOrder = ['ETF', '半导体', '光概念', 'SaaS', '大科技', '航天与新兴', '其他关注']
const themes = computed(() => {
  const entries = Object.entries((props.report?.instruments?.themes ?? {}) as Record<string, unknown[]>)
  return entries.sort(([left], [right]) => themeOrder.indexOf(left) - themeOrder.indexOf(right))
})
const activeTheme = ref('')
watch(themes, (value) => {
  if (!value.some(([name]) => name === activeTheme.value)) activeTheme.value = value[0]?.[0] ?? ''
}, { immediate: true })
const activeThemeRows = computed(() => themes.value.find(([name]) => name === activeTheme.value)?.[1] ?? [])
const activeSymbol = ref('')
const activeSymbolCard = computed(() => activeThemeRows.value.map(dict).find((item) => String(item.symbol) === activeSymbol.value) ?? null)

const optionData = computed(() => {
  const options = dict(props.report?.options)
  const phase = String(options.current_phase ?? props.report?.decision_phase ?? 'pre_close')
  const phases = phase === 'post_close_review'
    ? ['post_close_review', 'pre_close', 'pre_market']
    : phase === 'current_state'
      ? ['current_state', 'post_close_review', 'pre_close', 'pre_market']
      : phase === 'pre_close'
        ? ['pre_close', 'pre_market']
        : ['pre_market']
  const current = phases
    .map((name) => dict(options[name]))
    .find((value) => Array.isArray(value.symbols) && value.symbols.length > 0)
    ?? dict(options[phase] ?? options.pre_close ?? options.pre_market)
  return current
})
const optionSymbols = computed(() => {
  const current = optionData.value
  return Array.isArray(current.symbols) ? current.symbols.filter((item: unknown) => item && typeof item === 'object') as Dict[] : []
})
const postCloseAlignment = computed<PostCloseOptionAlignmentData | null>(() => {
  const value = dict(dict(props.report?.options).post_close_alignment)
  return Object.keys(value).length ? value as PostCloseOptionAlignmentData : null
})
const activeOptionSymbol = ref('')
watch(optionSymbols, (value) => {
  if (!value.some((item) => String(item.symbol) === activeOptionSymbol.value)) activeOptionSymbol.value = String(value[0]?.symbol ?? '')
}, { immediate: true })
const activeOption = computed(() => optionSymbols.value.find((item) => String(item.symbol) === activeOptionSymbol.value) ?? optionSymbols.value[0] ?? {})
const optionExpirations = computed(() => Array.isArray(activeOption.value.expirations) ? activeOption.value.expirations.map(dict) : [])
const activeExpiration = ref('')
watch(optionExpirations, (value) => {
  if (!value.some((item) => String(item.expiration) === activeExpiration.value)) activeExpiration.value = String(value[0]?.expiration ?? '')
}, { immediate: true })
const selectedExpiration = computed(() => optionExpirations.value.find((item) => String(item.expiration) === activeExpiration.value) ?? optionExpirations.value[0] ?? {})
const displayManifest = ref<ReportDisplayManifest | null>(null)
const displayOption = ref<ReportDisplayOptionPayload | null>(null)
const displayLoading = ref(false)
const displayError = ref('')
let displayRequestSequence = 0

async function loadDisplayProjection(): Promise<void> {
  const reportId = props.reportId
  const symbol = activeOptionSymbol.value
  const expiration = activeExpiration.value
  const requestSequence = ++displayRequestSequence
  displayOption.value = null
  displayError.value = ''
  if (!reportId || !symbol || !expiration) {
    displayManifest.value = null
    displayLoading.value = false
    return
  }
  displayLoading.value = true
  try {
    if (!displayManifest.value || displayManifest.value.report_id !== reportId) {
      displayManifest.value = await api.getReportDisplayManifest(reportId)
    }
    if (requestSequence !== displayRequestSequence) return
    displayOption.value = await api.getReportDisplayOptions(reportId, symbol, expiration)
  } catch (error) {
    if (requestSequence !== displayRequestSequence) return
    displayError.value = error instanceof Error ? error.message : '完整期权展示数据加载失败。'
  } finally {
    if (requestSequence === displayRequestSequence) displayLoading.value = false
  }
}

watch([() => props.reportId, activeOptionSymbol, activeExpiration], () => {
  void loadDisplayProjection()
}, { immediate: true })
const activeOptionOverview = computed(() => dict(activeOption.value.overview))
const optionIvHvSpread = computed(() => number(activeOptionOverview.value.iv_hv_spread)
  ?? ((number(activeOptionOverview.value.iv) !== null && number(activeOptionOverview.value.hv_30d) !== null)
    ? number(activeOptionOverview.value.iv)! - number(activeOptionOverview.value.hv_30d)!
    : null))
const optionIvHvRatio = computed(() => {
  const stored = number(activeOptionOverview.value.iv_hv_ratio)
  const iv = number(activeOptionOverview.value.iv)
  const hv = number(activeOptionOverview.value.hv_30d)
  return stored ?? (iv !== null && hv !== null && hv > 0 ? iv / hv : null)
})
const optionIvHvRegime = computed(() => {
  if (activeOptionOverview.value.iv_hv_regime) return String(activeOptionOverview.value.iv_hv_regime)
  const ratio = optionIvHvRatio.value
  if (ratio === null) return 'unknown'
  if (ratio < 0.7) return 'deep_discount'
  if (ratio < 0.9) return 'moderate_discount'
  if (ratio < 1.1) return 'matched'
  if (ratio < 1.4) return 'moderate_premium'
  return 'large_premium'
})

function dict(value: unknown): Dict { return value && typeof value === 'object' ? value as Dict : {} }
function number(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value))) return Number(value)
  return null
}
function text(value: unknown, fallback = '不可用'): string {
  if (value === null || value === undefined || value === '') return fallback
  if (typeof value === 'number') return formatNumber(value)
  return String(value)
}
function percent(value: unknown, fallback = '不可用'): string {
  const numeric = number(value)
  return numeric === null ? fallback : `${numeric > 100 || numeric < -100 ? numeric.toFixed(2) : numeric.toFixed(2)}%`
}
function json(value: unknown): string { return JSON.stringify(value ?? {}, null, 2) }
function quote(card: unknown): Dict {
  const value = dict(card)
  return dict(value.quote ?? value)
}
function technical(card: unknown): Dict {
  const value = dict(card)
  return dict(value.technical ?? dict(value.history).technical_indicators)
}
function metricNumber(value: unknown): number | null {
  const record = dict(value)
  return number(record.value ?? value)
}
function movingAverage(card: Dict, key: string): unknown { return dict(technical(card).moving_average)[key] }
function bollingerBand(card: Dict, key: string): Dict { return dict(dict(technical(card).bollinger)[key]) }
function relativeValue(card: Dict, group: 'excess_returns_percent' | 'beta' | 'correlation', window: string): number | null {
  return number(dict(dict(card.relative_strength)[group])[window])
}
function movingAverageState(card: Dict): string {
  const t = technical(card)
  const close = metricNumber(dict(dict(t.bollinger)['2_sigma']).current_price) ?? number(quote(card).last_price ?? quote(card).regular_price)
  const ma20 = number(dict(t.moving_average)['20d'])
  const ma50 = number(dict(t.moving_average)['50d'])
  const ma200 = number(dict(t.moving_average)['200d'])
  if (close === null || ma20 === null || ma50 === null) return '样本不足'
  if (close > ma20 && ma20 > ma50 && (ma200 === null || ma50 > ma200)) return '多头排列'
  if (close < ma20 && ma20 < ma50 && (ma200 === null || ma50 < ma200)) return '空头排列'
  if (close >= ma20) return '价格在 MA20 上方'
  return '价格在 MA20 下方'
}
function rsiState(value: unknown): string {
  const state = String(dict(value).state ?? 'unavailable')
  return ({ overbought: '超买', oversold: '超卖', positive: '偏强', negative: '偏弱', unavailable: '历史快照未采集' } as Record<string, string>)[state] ?? state
}
function rsiContextLabel(value: unknown): string {
  const classification = String(dict(value).classification ?? 'insufficient_data')
  return ({
    breakout_confirmed: '强势突破',
    extended_intact: '高位趋势完整',
    exhaustion_watch: '高位衰竭观察',
    exit_confirmed: '退出风险确认',
    breakdown_confirmed: '下跌加速',
    oversold_downtrend: '超卖趋势未止',
    reversal_watch: '反转观察',
    reversal_confirmed: '反转初步确认',
    base_forming: '筑底修复',
    neutral: '常规动量',
    insufficient_data: '复合判断不可用',
  } as Record<string, string>)[classification] ?? classification
}
function rsiSignalLabel(key: string): string {
  return ({
    breakout_20d: '突破前20日高点',
    breakout_60d: '突破前60日高点',
    breakdown_20d: '跌破前20日低点',
    breakdown_60d: '跌破前60日低点',
    bearish_divergence_20d: '20日顶背离',
    bullish_divergence_20d: '20日底背离',
    crossed_below_70: 'RSI跌回70下方',
    crossed_above_30: 'RSI重回30上方',
    bullish_ma_alignment: '均线多头排列',
    bearish_ma_alignment: '均线空头排列',
    macd_strengthening_up: 'MACD上行动量增强',
    macd_strengthening_down: 'MACD下行动量增强',
    rsi_slope_3d_up: 'RSI三日斜率向上',
    rsi_slope_3d_down: 'RSI三日斜率向下',
    high_volume_close_high: '放量且收盘靠近最高',
    high_volume_close_low: '放量且收盘靠近最低',
    wide_range_1_5_atr: '振幅超过1.5 ATR',
  } as Record<string, string>)[key] ?? key
}
function metricPercent(value: unknown, fallback = '不可用'): string {
  const numeric = metricNumber(value)
  return numeric === null ? fallback : `${numeric.toFixed(2)}%`
}
function ratioPercent(value: unknown, fallback = '不可用'): string {
  const numeric = number(value)
  return numeric === null ? fallback : `${(numeric * 100).toFixed(2)}%`
}
function distanceFromAverage(card: Dict, key: string): string {
  const average = number(movingAverage(card, key))
  const close = metricNumber(bollingerBand(card, '2_sigma').current_price) ?? number(quote(card).last_price ?? quote(card).regular_price)
  if (average === null || close === null || average === 0) return '不可用'
  const distance = (close / average - 1) * 100
  return `${distance >= 0 ? '+' : ''}${distance.toFixed(2)}%`
}
function qualityStatus(value: unknown): string { return String(value ?? 'unknown') }
function signedClass(value: unknown): string {
  const numeric = number(value)
  return numeric !== null && numeric >= 0 ? 'positive-text' : 'negative-text'
}
function eventResult(value: unknown): string {
  if (value === null || value === undefined || value === '') return '结果尚未采集'
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value)
  const record = dict(value)
  const summary = record.summary ?? record.headline ?? record.value
  return summary === undefined ? '已返回结构化结果（展开原始字段查看）' : String(summary)
}
function evidenceId(path: string): string { return `evidence-${path.replace(/[^a-zA-Z0-9_-]+/g, '-').replace(/^-|-$/g, '').toLowerCase()}` }

const marketCards = computed(() => {
  const market = dict(props.report?.market)
  const before = dict(market.pre_market)
  const phase = String(market.current_phase ?? props.report?.decision_phase ?? 'pre_close')
  const after = dict(market[phase] ?? market.pre_close ?? market.pre_market)
  const beforeQuote = quote(before.primary)
  const afterQuote = quote(after.primary)
  const crossAsset = Array.isArray(after.cross_asset_quotes) ? after.cross_asset_quotes.map(dict) : []
  const all = [afterQuote, ...crossAsset].filter((item, index, values) => item.symbol && values.findIndex((candidate) => candidate.symbol === item.symbol) === index)
  const preferred = ['SPY', 'QQQ', 'SMH', 'SOXX', 'IGV']
  const core = preferred.map((symbol) => all.find((item) => String(item.symbol).toUpperCase() === symbol)).filter((item): item is Dict => Boolean(item))
  const display = core.length ? core : all.slice(0, 4)
  return display.map((item) => {
    const beforeItem = item.symbol === beforeQuote.symbol ? beforeQuote : (Array.isArray(before.cross_asset_quotes) ? before.cross_asset_quotes.map(dict).find((candidate) => candidate.symbol === item.symbol) ?? {} : {})
    const left = number(beforeItem.last_price ?? beforeItem.regular_price)
    const right = number(item.last_price ?? item.regular_price)
    return { item, before: beforeItem, left, right, delta: left !== null && right !== null ? right - left : null }
  })
})

const crossAssetQuotes = computed(() => {
  const market = dict(props.report?.market)
  const phase = String(market.current_phase ?? props.report?.decision_phase ?? 'pre_close')
  const current = dict(market[phase] ?? market.pre_close ?? market.pre_market)
  return Array.isArray(current.cross_asset_quotes) ? current.cross_asset_quotes.map(dict).slice(0, 16) : []
})

const systematicFlows = computed(() => {
  const flows = dict(props.report?.systematic_flows)
  const phase = String(flows.current_phase ?? props.report?.decision_phase ?? 'pre_close')
  return dict(flows[phase] ?? flows.pre_close ?? flows.pre_market)
})
const systematicAssets = computed(() => Array.isArray(systematicFlows.value.assets) ? systematicFlows.value.assets.map(dict) : [])

const capitalFlows = computed(() => {
  const flows = dict(props.report?.capital_flows)
  const phase = String(flows.current_phase ?? props.report?.decision_phase ?? 'pre_close')
  return dict(flows[phase] ?? flows.pre_close ?? flows.pre_market)
})
const capitalFlowSymbols = computed(() => Array.isArray(capitalFlows.value.symbols) ? capitalFlows.value.symbols.map(dict) : [])

const instrumentRows = computed(() => activeThemeRows.value.map(dict).slice(0, 80).map((card) => {
  const q = quote(card)
  const t = technical(card)
  const bollinger = dict(t.bollinger)
  const macd = dict(t.macd_12_26_9)
  const rsi = dict(t.rsi14)
  const rsiContext = dict(t.rsi_context)
  const effort = dict(t.volume_effort_result)
  const relative20 = relativeValue(card, 'excess_returns_percent', '20d')
  return { card, q, t, bollinger, macd, rsi, rsiContext, effort, relative20 }
}))
const activeRsiContext = computed(() => dict(technical(activeSymbolCard.value).rsi_context))
const activeRsiMetrics = computed(() => dict(activeRsiContext.value.metrics))
const activeRsiSignals = computed(() => Object.entries(dict(activeRsiContext.value.signals))
  .filter(([, enabled]) => enabled === true)
  .map(([key]) => ({ key, label: rsiSignalLabel(key) })))
const instrumentSort = ref<'change' | 'symbol' | 'macd' | 'volume'>('change')
const sortedInstrumentRows = computed(() => [...instrumentRows.value].sort((left, right) => {
  if (instrumentSort.value === 'symbol') return String(left.card.symbol ?? '').localeCompare(String(right.card.symbol ?? ''))
  if (instrumentSort.value === 'macd') return (number(right.macd.histogram) ?? -Infinity) - (number(left.macd.histogram) ?? -Infinity)
  if (instrumentSort.value === 'volume') return (number(right.effort.volume_ratio_20d) ?? -Infinity) - (number(left.effort.volume_ratio_20d) ?? -Infinity)
  return (number(right.q.change_percent) ?? -Infinity) - (number(left.q.change_percent) ?? -Infinity)
}))

const exposure = computed(() => dict(selectedExpiration.value.exposure ?? selectedExpiration.value.exposure_totals))
const totals = computed(() => dict(selectedExpiration.value.exposure_totals ?? exposure.value.totals))
const walls = computed(() => dict(selectedExpiration.value.walls ?? exposure.value.walls))
const displayExpiration = computed(() => dict(displayOption.value?.data))
const displayStrikeStructure = computed(() => dict(displayExpiration.value.strike_structure))
const byStrike = computed(() => Array.isArray(displayStrikeStructure.value.rows)
  ? displayStrikeStructure.value.rows.map(dict).filter((item) => number(item.strike) !== null)
  : [])
const gammaProfile = computed(() => dict(selectedExpiration.value.spot_gamma_profile))
const displayGammaProfile = computed(() => dict(displayExpiration.value.gamma_profile))
const profilePoints = computed(() => Array.isArray(displayGammaProfile.value.points)
  ? displayGammaProfile.value.points.map(dict).filter((item) => number(item.spot ?? item.strike) !== null)
  : [])
const gammaRange = computed(() => {
  const values = profilePoints.value.map((item) => number(item.net_gex ?? item.gex)).filter((value): value is number => value !== null)
  const max = Math.max(Math.abs(Math.min(...values, 0)), Math.abs(Math.max(...values, 0)), 1)
  return { min: Math.min(...values, 0), max: Math.max(...values, 0), abs: max }
})
const profileSpot = computed(() => number(
  displayGammaProfile.value.current_spot
    ?? activeOption.value.spot
    ?? gammaProfile.value.current_spot,
))
const profileGammaFlip = computed(() => number(
  displayGammaProfile.value.primary_gamma_flip
    ?? gammaProfile.value.primary_gamma_flip,
))
const profileGammaFlips = computed(() => {
  const projected = (Array.isArray(displayGammaProfile.value.flips) ? displayGammaProfile.value.flips : [])
    .map(dict)
    .map((item) => ({
      spot: number(item.spot),
      isPrimary: Boolean(item.is_primary),
    }))
    .filter((item): item is { spot: number; isPrimary: boolean } => item.spot !== null)
  if (projected.length) return projected
  return profileGammaFlip.value === null
    ? []
    : [{ spot: profileGammaFlip.value, isPrimary: true }]
})

function wall(label: string, key: string): { label: string; strike: unknown; exposure: unknown } {
  const value = dict(walls.value[key])
  return { label, strike: value.strike, exposure: value.exposure ?? value.value }
}
const wallRows = computed(() => [
  wall('Call DEX Wall', 'call_dex'),
  wall('Put DEX Wall', 'put_dex'),
  wall('Call Gamma Wall', 'call_gamma'),
  wall('Put Gamma Wall', 'put_gamma'),
  wall('Absolute Gamma Wall', 'absolute_gamma'),
  wall('Net DEX Wall', 'net_dex'),
])

function strikeClass(item: Dict): string {
  const regime = String(item.gamma_regime ?? 'neutral').toLowerCase()
  if (regime.includes('positive')) return 'positive'
  if (regime.includes('negative')) return 'negative'
  const netGex = number(item.net_gex ?? item.modeled_net_gex)
  if (netGex !== null && netGex > 0) return 'positive'
  if (netGex !== null && netGex < 0) return 'negative'
  return 'neutral'
}
function strikeFocus(item: Dict): string[] {
  const strike = number(item.strike)
  if (strike === null) return []
  const labels: string[] = []
  const spot = number(activeOption.value.spot ?? gammaProfile.value.current_spot)
  if (spot !== null && Math.abs(strike - spot) <= Math.max(0.01, Math.abs(spot) * 0.006)) labels.push('Spot')
  for (const row of wallRows.value) if (number(row.strike) === strike) labels.push(row.label.replace(/ Wall$/, ''))
  if (number(selectedExpiration.value.max_pain) === strike) labels.push('Max Pain')
  return [...new Set(labels)]
}
function barStyle(value: unknown, color = 'positive-bar'): Record<string, string> {
  const numeric = number(value)
  if (numeric === null || gammaRange.value.abs <= 0) return { height: '0px', [numeric !== null && numeric < 0 ? 'bottom' : 'top']: '50%' }
  const height = `${Math.max(2, Math.min(46, Math.abs(numeric) / gammaRange.value.abs * 46))}%`
  return numeric >= 0 ? { height, bottom: '50%' } : { height, top: '50%' }
}
function profilePolyline(): string {
  if (!profilePoints.value.length) return ''
  const minX = Math.min(...profilePoints.value.map((item) => number(item.spot ?? item.strike) ?? 0))
  const maxX = Math.max(...profilePoints.value.map((item) => number(item.spot ?? item.strike) ?? 0), minX + 1)
  return profilePoints.value.map((item) => {
    const x = number(item.spot ?? item.strike) ?? minX
    const y = number(item.net_gex ?? item.gex) ?? 0
    const px = ((x - minX) / (maxX - minX)) * 100
    const py = 50 - (y / gammaRange.value.abs) * 42
    return `${px.toFixed(2)},${Math.max(5, Math.min(95, py)).toFixed(2)}`
  }).join(' ')
}
function profileXForPrice(price: number | null): number | null {
  if (price === null || !profilePoints.value.length) return null
  const values = profilePoints.value
    .map((item) => number(item.spot ?? item.strike))
    .filter((value): value is number => value !== null)
  if (!values.length) return null
  const minX = Math.min(...values)
  const maxX = Math.max(...values, minX + 1)
  return Math.max(0, Math.min(100, ((price - minX) / (maxX - minX)) * 100))
}
const profileSpotX = computed(() => profileXForPrice(profileSpot.value))
const profileGammaFlipX = computed(() => profileXForPrice(profileGammaFlip.value))
const profileGammaFlipMarkers = computed(() => profileGammaFlips.value
  .map((item, index) => ({
    ...item,
    x: profileXForPrice(item.spot),
    key: `${item.spot}-${index}`,
  }))
  .filter((item) => item.x !== null))
</script>

<template>
  <div v-if="!report" class="empty-panel"><p>技术整理报告尚未生成。</p></div>
  <template v-else>
    <div class="report-toolbar">
      <span class="live-badge">程序生成 · {{ report.schema_version }}</span>
      <span class="subtle">生成 {{ formatDate(report.generated_at) }} · 截止 {{ formatDate(report.cutoff_time) }}</span>
      <span class="subtle">AI 不参与此 Tab 的结论。</span>
    </div>

    <nav class="technical-secondary-tabs" aria-label="技术整理报告二级标签">
      <button
        v-for="item in secondaryTabs"
        :key="item.id"
        type="button"
        :class="{ active: selectedSection === item.id }"
        :aria-current="selectedSection === item.id ? 'page' : undefined"
        @click="emit('select-section', item.id)"
      >
        <strong>{{ item.label }}</strong>
        <small>{{ item.hint }}</small>
      </button>
    </nav>

    <template v-if="selectedSection === 'overview'">
    <section :id="evidenceId('quality')" class="report-section">
      <div class="report-section-heading"><div><p class="eyebrow">QUALITY / PROVENANCE</p><h2>数据质量摘要</h2></div><span class="status-badge" :data-status="qualityStatus(report.quality?.status)">{{ qualityStatus(report.quality?.status) }}</span></div>
      <div class="report-metric-grid report-metric-grid-4">
        <div class="report-metric"><span>来源</span><strong>{{ text(report.source?.label ?? report.source?.dataset_key) }}</strong></div>
        <div class="report-metric"><span>快照 / Run</span><strong>{{ text(report.source?.snapshot_count ?? report.source?.run_count) }}</strong></div>
        <div class="report-metric"><span>标的覆盖</span><strong>{{ instrumentRows.length }} / {{ themes.reduce((sum, [, values]) => sum + values.length, 0) }}</strong></div>
        <div class="report-metric"><span>缺口</span><strong>{{ report.omissions?.length ?? 0 }}</strong></div>
      </div>
      <p v-if="report.quality?.message" class="report-note quality-note">{{ String(report.quality.message) }}</p>
      <div v-if="Array.isArray(report.quality?.warnings) && report.quality.warnings.length" class="notice-list"><span v-for="warning in report.quality.warnings.slice(0, 5)" :key="String(warning)">⚠ {{ String(warning) }}</span></div>
    </section>

    <section v-if="systematicAssets.length" :id="evidenceId('systematic_flows')" class="report-section">
      <div class="report-section-heading"><div><p class="eyebrow">CTA / SYSTEMATIC FLOWS</p><h2>系统化仓位与边际压力</h2></div><span class="status-badge" :data-status="qualityStatus(dict(systematicFlows.quality).status)">{{ text(systematicFlows.model_state) }}</span></div>
      <div class="report-metric-grid report-metric-grid-4">
        <div class="report-metric"><span>来源口径</span><strong>{{ text(systematicFlows.source_mode) }}</strong></div>
        <div class="report-metric"><span>有效信号</span><strong>{{ text(dict(systematicFlows.portfolio).signal_count) }}</strong></div>
        <div class="report-metric"><span>未加权 Gross</span><strong>{{ text(dict(systematicFlows.portfolio).unweighted_gross_exposure) }}</strong></div>
        <div class="report-metric"><span>未加权 Net</span><strong>{{ text(dict(systematicFlows.portfolio).unweighted_net_exposure) }}</strong></div>
      </div>
      <div class="report-table-wrap"><table class="report-table"><thead><tr><th>代理</th><th>资产组</th><th>模型仓位</th><th>边际变化</th><th>压力</th><th>机械动作</th><th>截至</th></tr></thead><tbody><tr v-for="item in systematicAssets" :key="String(item.symbol)"><td><strong>{{ text(item.symbol) }}</strong><small>{{ text(item.proxy_for, '') }}</small></td><td>{{ text(item.asset_class) }}</td><td :class="signedClass(item.target_exposure)">{{ text(item.target_exposure) }}</td><td :class="signedClass(item.exposure_change)">{{ text(item.exposure_change) }}</td><td :class="signedClass(item.pressure_index)">{{ text(item.pressure_index) }}</td><td>{{ text(item.mechanical_action) }}</td><td>{{ text(item.as_of) }}</td></tr></tbody></table></div>
      <p class="report-note quality-note">ETF 日线代理估算，不是已观察到的 CTA 持仓或真实资金流；跨资产 Gross/Net 尚未做相关性调整。</p>
    </section>

    <section v-if="capitalFlowSymbols.length" :id="evidenceId('capital_flows')" class="report-section">
      <div class="report-section-heading"><div><p class="eyebrow">ORDER-SIZE CAPITAL FLOW</p><h2>订单金额分档资金流信号</h2></div><span class="status-badge" :data-status="qualityStatus(capitalFlows.quality_status)">截至 {{ text(capitalFlows.as_of_date) }}</span></div>
      <div class="report-table-wrap"><table class="report-table"><thead><tr><th>ETF</th><th>确定性信号</th><th>置信度</th><th>连续大额流出</th><th>最新大额单</th><th>最新中小额单</th><th>样本</th></tr></thead><tbody><tr v-for="item in capitalFlowSymbols" :key="String(item.symbol)"><td><strong>{{ text(item.symbol) }}</strong></td><td>{{ text(dict(item.signal_projection).signal_label ?? dict(item.signal_projection).signal) }}</td><td>{{ number(dict(item.signal_projection).confidence) === null ? '不可用' : `${formatNumber(number(dict(item.signal_projection).confidence)! * 100)}%` }}</td><td>{{ text(dict(dict(item.signal_projection).features).prior_block_outflow_streak_30d) }}</td><td :class="signedClass(dict(dict(item.signal_projection).features).latest_block_flow)">{{ text(dict(dict(item.signal_projection).features).latest_block_flow) }}</td><td :class="signedClass(dict(dict(item.signal_projection).features).latest_mid_small_flow)">{{ text(dict(dict(item.signal_projection).features).latest_mid_small_flow) }}</td><td>{{ text(item.cached_trading_days) }} 日</td></tr></tbody></table></div>
      <p class="report-note quality-note">按成交订单金额分档的主动净流量，不代表已识别的机构、散户或账户身份；信号只作价格、量能和趋势的辅助确认。展开原始数据可查看最近 5 个交易日。</p>
      <details class="report-card"><summary>查看最近 5 日精简资金流</summary><pre class="compact-json">{{ json(capitalFlowSymbols.map((item) => ({ symbol: item.symbol, ...dict(item.signal_projection) }))) }}</pre></details>
    </section>

    <section :id="evidenceId('market')" class="report-section">
      <div class="report-section-heading"><div><p class="eyebrow">MARKET / PRE-MARKET → PRE-CLOSE</p><h2>市场总览</h2></div><span class="subtle">盘前到收盘前的价格轨迹</span></div>
      <div v-if="marketCards.length" class="market-slope-grid">
        <article v-for="card in marketCards" :key="String(card.item.symbol)" class="market-slope-card">
          <div class="report-card-title"><strong>{{ text(card.item.symbol) }}</strong><span>{{ text(card.item.label, 'market') }}</span></div>
          <div class="slope-values"><span>{{ text(card.left) }}</span><span :class="card.delta !== null && card.delta >= 0 ? 'positive-text' : 'negative-text'">{{ card.delta === null ? '不可用' : `${card.delta >= 0 ? '+' : ''}${formatNumber(card.delta)}` }}</span><strong>{{ text(card.right) }}</strong></div>
          <div class="slope-track"><span class="slope-dot slope-start" :style="{ left: card.left !== null && card.right !== null && card.left !== card.right ? `${Math.max(5, Math.min(95, card.left / Math.max(card.left, card.right) * 100))}%` : '24%' }"></span><span class="slope-line" :class="card.delta !== null && card.delta >= 0 ? 'positive-line' : 'negative-line'"></span><span class="slope-dot slope-end"></span></div>
          <small class="slope-caption">盘前 {{ text(card.before.quote_time ?? card.before.quoteTime) }} → 收盘前 {{ text(card.item.quote_time ?? card.item.quoteTime) }}</small>
        </article>
      </div>
      <div v-else class="empty-panel"><p>市场价格暂不可用。</p></div>
      <div v-if="crossAssetQuotes.length" class="report-table-wrap market-cross-table">
        <table class="report-table"><thead><tr><th>跨资产</th><th>价格</th><th>涨跌</th><th>来源 / 质量</th></tr></thead><tbody><tr v-for="item in crossAssetQuotes" :key="String(item.symbol)"><td><strong>{{ text(item.symbol) }}</strong><small>{{ text(item.label, '') }}</small></td><td>{{ text(item.last_price ?? item.regular_price) }}</td><td :class="signedClass(item.change_percent)">{{ percent(item.change_percent) }}</td><td>{{ text(item.source) }} · {{ qualityStatus(item.quality_status) }}</td></tr></tbody></table>
      </div>
    </section>

    <section class="report-section report-footnote-grid"><details class="report-card"><summary>查看配对变化数据</summary><pre class="compact-json">{{ json({ market: report.market?.paired_changes, instruments: report.instruments?.paired_changes, options: report.options?.paired_changes }) }}</pre></details><details class="report-card"><summary>查看已知缺口</summary><pre class="compact-json">{{ json(report.omissions) }}</pre></details></section>
    </template>

    <section v-else-if="selectedSection === 'instruments'" :id="evidenceId('instruments')" class="report-section">
      <div class="report-section-heading"><div><p class="eyebrow">INSTRUMENTS / THEMES</p><h2>个股技术矩阵</h2></div><label class="table-sort"><span>排序</span><select v-model="instrumentSort"><option value="change">涨跌幅</option><option value="volume">量比</option><option value="macd">MACD 柱体</option><option value="symbol">Symbol</option></select></label></div>
      <nav class="theme-tabs" aria-label="个股题材"><button v-for="[theme, values] in themes" :key="theme" type="button" :class="{ active: activeTheme === theme }" @click="activeTheme = theme">{{ theme }} <span>{{ values.length }}</span></button></nav>
      <p class="instrument-matrix-guide">先看趋势、动量、风险和相对强弱；点击一行展开该标的的完整技术全景。RSI14 从新采集快照开始提供，历史报告保持不可变。</p>
      <div v-if="instrumentRows.length" class="report-table-wrap instrument-matrix-wrap">
        <table class="report-table instrument-matrix">
          <thead><tr><th>Symbol</th><th>价格 / 日变动</th><th>RS20 vs QQQ</th><th>趋势结构</th><th>RSI14</th><th>MACD</th><th>RV20 / ATR14</th><th>量价</th><th>质量</th></tr></thead>
          <tbody><tr v-for="row in sortedInstrumentRows" :key="String(row.card.symbol)" :class="{ 'selected-row': activeSymbol === String(row.card.symbol) }" @click="activeSymbol = String(row.card.symbol)">
            <td><strong class="mono">{{ text(row.card.symbol) }}</strong><small>{{ text(row.card.label ?? row.card.theme, '') }}</small></td>
            <td><span class="matrix-price">{{ text(row.q.last_price ?? row.q.regular_price) }}</span><small :class="signedClass(row.q.change_percent)">{{ percent(row.q.change_percent) }}</small></td>
            <td :class="signedClass(row.relative20)"><strong>{{ percent(row.relative20) }}</strong><small>5D {{ percent(relativeValue(row.card, 'excess_returns_percent', '5d')) }}</small></td>
            <td><span>{{ movingAverageState(row.card) }}</span><small>vs MA20 {{ distanceFromAverage(row.card, '20d') }}</small></td>
            <td><strong>{{ text(row.rsi.value, '历史未采集') }}</strong><small>{{ row.rsiContext.available ? rsiContextLabel(row.rsiContext) : rsiState(row.rsi) }}</small></td>
            <td><span>{{ text(row.macd.momentum) }}</span><small>Hist {{ text(row.macd.histogram) }}</small></td>
            <td><span>{{ metricPercent(dict(row.t.realized_volatility)['20d']) }}</span><small>ATR {{ metricPercent(row.t.atr14_percent) }}</small></td>
            <td><span>量比 {{ text(row.effort.volume_ratio_20d) }}</span><small>{{ text(row.effort.signal) }}</small></td>
            <td><span class="status-badge" :data-status="qualityStatus(row.t.quality_status ?? row.card.quality_status)">{{ qualityStatus(row.t.quality_status ?? row.card.quality_status) }}</span></td>
          </tr></tbody>
        </table>
      </div>
      <div v-else class="empty-panel"><p>暂无个股技术数据。</p></div>
      <article v-if="activeSymbolCard" class="instrument-detail-card">
        <div class="report-card-title"><div><p class="eyebrow">SYMBOL DETAIL</p><strong>{{ text(activeSymbolCard.symbol) }}</strong></div><button type="button" class="secondary-button" @click="activeSymbol = ''">关闭</button></div>
        <div class="technical-detail-sections">
          <section><h3>收益与趋势</h3><div class="instrument-detail-grid instrument-detail-grid-6"><div v-for="window in ['1d', '5d', '20d', '60d', '120d', '252d']" :key="window"><span>收益 {{ window.toUpperCase() }}</span><strong :class="signedClass(dict(technical(activeSymbolCard).returns_percent)[window])">{{ percent(dict(technical(activeSymbolCard).returns_percent)[window]) }}</strong></div></div><div class="instrument-detail-grid instrument-detail-grid-5"><div v-for="window in ['10d', '20d', '50d', '100d', '200d']" :key="window"><span>MA {{ window.replace('d', '') }}</span><strong>{{ text(movingAverage(activeSymbolCard, window)) }}</strong><small>价格距离 {{ distanceFromAverage(activeSymbolCard, window) }}</small></div></div></section>
          <section><h3>动量与波动</h3><div class="instrument-detail-grid"><div><span>RSI14 · Wilder</span><strong>{{ text(dict(technical(activeSymbolCard).rsi14).value, '历史快照未采集') }} · {{ rsiState(technical(activeSymbolCard).rsi14) }}</strong><small>日变动 {{ text(dict(technical(activeSymbolCard).rsi14).change) }}</small></div><div><span>MACD DIF / DEA / Hist</span><strong>{{ text(dict(technical(activeSymbolCard).macd_12_26_9).dif) }} / {{ text(dict(technical(activeSymbolCard).macd_12_26_9).dea) }} / {{ text(dict(technical(activeSymbolCard).macd_12_26_9).histogram) }}</strong><small>{{ text(dict(technical(activeSymbolCard).macd_12_26_9).momentum) }}</small></div><div><span>RV10 / RV20 / RV60</span><strong>{{ metricPercent(dict(technical(activeSymbolCard).realized_volatility)['10d']) }} / {{ metricPercent(dict(technical(activeSymbolCard).realized_volatility)['20d']) }} / {{ metricPercent(dict(technical(activeSymbolCard).realized_volatility)['60d']) }}</strong></div><div><span>ATR14 / ATR%</span><strong>{{ text(metricNumber(technical(activeSymbolCard).atr14)) }} / {{ metricPercent(technical(activeSymbolCard).atr14_percent) }}</strong></div></div></section>
          <section v-if="activeRsiContext.available" class="rsi-context-section"><div class="rsi-context-heading"><div><h3>RSI 复合判断</h3><p>{{ text(activeRsiContext.interpretation) }}</p></div><span class="rsi-context-badge" :data-context="String(activeRsiContext.classification)">{{ rsiContextLabel(activeRsiContext) }}</span></div><div class="instrument-detail-grid"><div><span>{{ activeRsiContext.continuation_direction === 'down' ? '下跌延续分' : '上涨延续分' }}</span><strong>{{ text(activeRsiContext.continuation_score) }} / {{ text(activeRsiContext.score_scale) }}</strong></div><div><span>{{ activeRsiContext.continuation_direction === 'down' ? '反转修复分' : '衰竭风险分' }}</span><strong>{{ text(activeRsiContext.reversal_score) }} / {{ text(activeRsiContext.score_scale) }}</strong></div><div><span>RSI 3D / 5D 斜率</span><strong>{{ text(activeRsiMetrics.rsi_slope_3d) }} / {{ text(activeRsiMetrics.rsi_slope_5d) }}</strong></div><div><span>连续超买 / 超卖</span><strong>{{ text(activeRsiMetrics.overbought_days, '0') }} / {{ text(activeRsiMetrics.oversold_days, '0') }} 日</strong></div></div><div v-if="activeRsiSignals.length" class="rsi-signal-list"><span v-for="signal in activeRsiSignals" :key="signal.key">{{ signal.label }}</span></div><p class="report-note quality-note">程序生成的动量上下文，会提供给 AI 作为证据；不会单独转换成买入、卖出或持仓指令。</p></section>
          <section><h3>相对强弱与位置</h3><div class="instrument-detail-grid"><div><span>超额收益 vs QQQ · 5 / 20 / 60D</span><strong>{{ percent(relativeValue(activeSymbolCard, 'excess_returns_percent', '5d')) }} / {{ percent(relativeValue(activeSymbolCard, 'excess_returns_percent', '20d')) }} / {{ percent(relativeValue(activeSymbolCard, 'excess_returns_percent', '60d')) }}</strong></div><div><span>Beta · 20 / 60D</span><strong>{{ text(relativeValue(activeSymbolCard, 'beta', '20d')) }} / {{ text(relativeValue(activeSymbolCard, 'beta', '60d')) }}</strong></div><div><span>Correlation · 20 / 60D</span><strong>{{ text(relativeValue(activeSymbolCard, 'correlation', '20d')) }} / {{ text(relativeValue(activeSymbolCard, 'correlation', '60d')) }}</strong></div><div><span>距 52 周高 / 低</span><strong>{{ percent(dict(technical(activeSymbolCard).high_low_distance_percent)['252d_high']) }} / {{ percent(dict(technical(activeSymbolCard).high_low_distance_percent)['252d_low']) }}</strong></div></div></section>
          <section><h3>布林与量价</h3><div class="instrument-detail-grid"><div><span>Bollinger 1σ / 2σ / 3σ 位置</span><strong>{{ percent(bollingerBand(activeSymbolCard, '1_sigma').position_percent) }} / {{ percent(bollingerBand(activeSymbolCard, '2_sigma').position_percent) }} / {{ percent(bollingerBand(activeSymbolCard, '3_sigma').position_percent) }}</strong><small>2σ 带宽 {{ metricPercent(dict(technical(activeSymbolCard).bollinger).bandwidth_20) }}</small></div><div><span>成交量 / 20D 均量</span><strong>{{ text(dict(technical(activeSymbolCard).volume_effort_result).volume_ratio_20d) }}×</strong><small>{{ text(dict(technical(activeSymbolCard).volume_effort_result).effort) }} effort</small></div><div><span>Range / ATR · 收盘位置</span><strong>{{ text(dict(technical(activeSymbolCard).volume_effort_result).range_atr_ratio) }} / {{ ratioPercent(dict(technical(activeSymbolCard).volume_effort_result).close_location_ratio) }}</strong></div><div><span>Effort / Result 信号</span><strong>{{ text(dict(technical(activeSymbolCard).volume_effort_result).combination) }}</strong><small>{{ text(dict(technical(activeSymbolCard).volume_effort_result).signal) }} · {{ text(dict(technical(activeSymbolCard).volume_effort_result).signal_strength) }}</small></div></div></section>
        </div>
        <details><summary>查看该标的原始字段</summary><pre class="compact-json">{{ json(activeSymbolCard) }}</pre></details>
      </article>
    </section>

    <section v-else-if="selectedSection === 'options'" :id="evidenceId('options')" class="report-section">
      <div class="report-section-heading"><div><p class="eyebrow">OPTIONS / DEX · GEX · GAMMA</p><h2>期权结构证据</h2></div><span class="subtle">按标的与到期日查看</span></div>
      <PostCloseOptionAlignment :alignment="postCloseAlignment" :focus-symbol="activeOptionSymbol" :focus-expiration="activeExpiration" />
      <div v-if="optionSymbols.length" class="options-report-shell">
        <nav class="theme-tabs" aria-label="期权标的"><button v-for="item in optionSymbols" :key="String(item.symbol)" type="button" :class="{ active: activeOptionSymbol === String(item.symbol) }" @click="activeOptionSymbol = String(item.symbol)">{{ text(item.symbol) }} <span>{{ Array.isArray(item.expirations) ? item.expirations.length : 0 }}</span></button></nav>
        <div class="option-expiration-tabs"><button v-for="item in optionExpirations" :key="String(item.expiration)" type="button" :class="{ active: activeExpiration === String(item.expiration) }" @click="activeExpiration = String(item.expiration)">{{ text(item.expiration) }} <small>{{ text(item.days_to_expiry) }} DTE</small></button></div>
        <article class="report-card option-structure-card">
          <div class="report-card-title"><strong>{{ text(activeOption.symbol) }} · {{ text(selectedExpiration.expiration) }}</strong><span>spot {{ text(activeOption.spot ?? gammaProfile.current_spot) }}</span></div>
          <div class="report-metric-grid report-metric-grid-4 option-summary-metrics"><div class="report-metric"><span>综合 IV / HV30</span><strong>{{ text(activeOptionOverview.iv) }}% / {{ text(activeOptionOverview.hv_30d) }}%</strong></div><div class="report-metric"><span>IV − HV30</span><strong :class="signedClass(optionIvHvSpread)">{{ text(optionIvHvSpread) }} pp</strong></div><div class="report-metric"><span>IV / HV30</span><strong>{{ text(optionIvHvRatio) }}</strong></div><div class="report-metric"><span>波动率定价</span><strong>{{ optionIvHvRegime }}</strong><small>{{ text(activeOptionOverview.term_match_method, 'provider_composite_proxy') }}</small></div><div class="report-metric"><span>Max Pain</span><strong>{{ text(selectedExpiration.max_pain) }}</strong></div><div class="report-metric"><span>Expected Move</span><strong>{{ text(dict(selectedExpiration.expected_move).amount) }}</strong></div><div class="report-metric"><span>Call / Put DEX</span><strong>{{ text(totals.call_dex) }} / {{ text(totals.put_dex) }}</strong></div><div class="report-metric"><span>Net GEX</span><strong>{{ text(totals.modeled_net_gex) }}</strong></div><div class="report-metric"><span>Gamma Flip</span><strong>{{ text(gammaProfile.primary_gamma_flip) }}</strong></div><div class="report-metric"><span>Spot Gamma</span><strong>{{ text(gammaProfile.current_spot_net_gex) }}</strong></div><div class="report-metric"><span>正 / 负区间</span><strong>{{ text(selectedExpiration.gamma_zone_count, '0') }} / {{ text(Array.isArray(selectedExpiration.gamma_zones) ? selectedExpiration.gamma_zones.filter((item: unknown) => String(dict(item).sign).toLowerCase().includes('negative')).length : 0, '0') }}</strong></div><div class="report-metric"><span>Gamma 状态</span><strong>{{ text(gammaProfile.gamma_regime ?? selectedExpiration.gamma_regime) }}</strong></div></div>
          <div class="options-visual-grid">
            <div><div class="visual-title"><strong>DEX / GEX 按行权价</strong><span>{{ displayLoading ? '正在读取完整链…' : byStrike.length ? `${byStrike.length} strikes · 完整展示投影` : '完整展示数据不可用' }}</span></div><div v-if="displayLoading" class="chart-unavailable">正在从报告展示投影读取完整行权价结构，不使用 AI 输入包。</div><div v-else-if="byStrike.length" class="horizontal-exposure-scroll"><div class="horizontal-exposure-chart"><div class="horizontal-zero-axis"></div><div v-for="item in byStrike" :key="String(item.strike)" class="strike-column" :class="[`gamma-${strikeClass(item)}`, { 'focus-row': strikeFocus(item).length, 'focus-spot': strikeFocus(item).includes('Spot'), 'focus-wall': strikeFocus(item).some((label) => label.includes('Wall')), 'focus-max-pain': strikeFocus(item).includes('Max Pain') }]" :title="`${item.strike} · ${strikeFocus(item).join(', ')}`"><div class="vertical-exposure-bars"><span class="vertical-bar dex-vertical" :class="number(item.net_dex) !== null && number(item.net_dex)! >= 0 ? 'positive-bar' : 'negative-bar'" :style="barStyle(item.net_dex)"></span><span class="vertical-bar gex-vertical" :class="number(item.modeled_net_gex ?? item.net_gex) !== null && number(item.modeled_net_gex ?? item.net_gex)! >= 0 ? 'positive-bar' : 'negative-bar'" :style="barStyle(item.modeled_net_gex ?? item.net_gex)"></span></div><span class="horizontal-strike-label" :class="{ 'spot-nearby': strikeFocus(item).includes('Spot') }">{{ text(item.strike) }}</span><span class="horizontal-focus-badges"><small v-for="label in strikeFocus(item).slice(0, 2)" :key="label">{{ label.replace(' Gamma', '') }}</small></span></div></div></div><div v-else class="chart-unavailable">{{ displayError || '完整行权价展示数据不可用；请查看数据质量说明。' }}</div><div class="chart-legend"><span><i class="legend-dex"></i>DEX</span><span><i class="legend-gex"></i>GEX</span><span><i class="legend-positive"></i>正 Gamma</span><span><i class="legend-negative"></i>负 Gamma</span></div></div>
            <div><div class="visual-title"><strong>Spot Gamma Profile / Flip</strong><span>{{ displayLoading ? '正在读取…' : profilePoints.length ? `${profilePoints.length} points · 完整展示投影` : '完整 profile 不可用' }}</span></div><div v-if="displayLoading" class="chart-unavailable">正在从报告展示投影读取全部 Gamma Profile 点。</div><div v-else-if="profilePoints.length" class="spot-profile-chart"><svg viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="Spot Gamma Profile"><line x1="0" y1="50" x2="100" y2="50" class="profile-zero-line"/><polyline :points="profilePolyline()" class="profile-line" fill="none"/><line v-if="profileSpotX !== null" :x1="profileSpotX" y1="5" :x2="profileSpotX" y2="95" class="profile-spot-line"/><line v-if="profileGammaFlipX !== null" :x1="profileGammaFlipX" y1="5" :x2="profileGammaFlipX" y2="95" class="profile-flip-line"/><g v-for="marker in profileGammaFlipMarkers" :key="marker.key"><circle :cx="marker.x ?? 0" cy="50" r="2" class="profile-zero-point" :class="{ primary: marker.isPrimary }"/><title>{{ marker.isPrimary ? 'Gamma Flip' : '0 GEX' }} {{ text(marker.spot) }}</title></g></svg><div class="profile-axis-labels"><span>负 Gamma</span><span>0</span><span>正 Gamma</span></div><div class="profile-marker-labels"><span v-if="profileSpot !== null" class="profile-spot-label">现价 {{ text(profileSpot) }}</span><span v-for="marker in profileGammaFlipMarkers" :key="`label-${marker.key}`" :class="marker.isPrimary ? 'profile-flip-label' : 'profile-zero-label'">{{ marker.isPrimary ? 'Gamma Flip' : '0 GEX' }} {{ text(marker.spot) }}</span></div></div><div v-else class="chart-unavailable">{{ displayError || '完整 Gamma Profile 数据不可用；请查看数据质量说明。' }}</div></div>
          </div>
          <div class="option-walls-grid"><div v-for="row in wallRows" :key="row.label" class="wall-chip"><span>{{ row.label }}</span><strong>{{ text(row.strike) }}</strong><small>{{ text(row.exposure) }}</small></div></div>
          <div v-if="byStrike.length" class="report-table-wrap option-strike-table"><table class="report-table"><thead><tr><th>Strike</th><th>Gamma 区间</th><th>Call DEX</th><th>Put DEX</th><th>Net DEX</th><th>Net GEX</th><th>关注</th></tr></thead><tbody><tr v-for="item in byStrike" :key="String(item.strike)" :class="`gamma-row-${strikeClass(item)}`"><td><strong>{{ text(item.strike) }}</strong></td><td><span :class="`gamma-regime-tag gamma-${strikeClass(item)}`">{{ strikeClass(item) }}</span></td><td>{{ text(item.call_dex) }}</td><td>{{ text(item.put_dex) }}</td><td>{{ text(item.net_dex) }}</td><td>{{ text(item.modeled_net_gex ?? item.net_gex) }}</td><td><span v-for="label in strikeFocus(item)" :key="label" class="focus-label">{{ label }}</span><span v-if="!strikeFocus(item).length" class="subtle">—</span></td></tr></tbody></table></div>
          <details><summary>查看 gamma 区间与原始字段</summary><pre class="compact-json">{{ json({ gamma_zones: selectedExpiration.gamma_zones, strike_gex_sign_changes: selectedExpiration.strike_gex_sign_changes, raw: selectedExpiration }) }}</pre></details>
        </article>
      </div>
      <div v-else class="empty-panel"><p>暂无可用期权结构证据。</p></div>
    </section>

    <section v-else-if="selectedSection === 'events'" :id="evidenceId('events')" class="report-section">
      <div class="report-section-heading"><div><p class="eyebrow">EVENTS / TIMELINE</p><h2>事件时间轴</h2></div><span class="subtle">程序采集的事件，不混入 AI 推断</span></div>
      <div v-if="Array.isArray(report.events?.records) && report.events.records.length" class="event-timeline"><article v-for="event in report.events.records" :key="String(event.id ?? event.event_key ?? event.title)" class="event-timeline-item"><span class="timeline-dot"></span><div><div class="report-card-title"><strong>{{ text(event.title ?? event.event_type) }}</strong><span class="status-badge" :data-status="String(event.status ?? 'expected')">{{ text(event.status) }}</span></div><p>{{ text(event.scheduled_at ?? event.occurred_at) }} · {{ text(event.subject) }}</p><small>{{ eventResult(event.result) }}</small></div></article></div><div v-else class="empty-panel"><p>暂无事件记录。</p></div>
    </section>

  </template>
</template>
