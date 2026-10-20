<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import AppShell from '@/components/AppShell.vue'
import RemoteDecisionPanel from '@/components/decision/RemoteDecisionPanel.vue'
import { api } from '@/api/client'
import type { ObservationRunSummary } from '@/types/api'
import type {
  CrossSectionCatalogItem,
  CrossSectionComparison,
  CrossSectionLensType,
  CrossSectionProjection,
  CrossSectionRow,
} from '@/types/crossSection'
import type { RemoteDecisionSource } from '@/types/remoteDecision'

const props = defineProps<{ lensType: CrossSectionLensType }>()
const route = useRoute()
const router = useRouter()

const catalog = ref<CrossSectionCatalogItem[]>([])
const runs = ref<ObservationRunSummary[]>([])
const projection = ref<CrossSectionProjection | null>(null)
const selectedRunId = ref('')
const selectedLensId = ref('')
const loading = ref(true)
const loadingProjection = ref(false)
const error = ref('')
const pageReady = ref(false)
const automaticFallbackRunId = ref('')
let requestSequence = 0

const activeLensType = computed<CrossSectionLensType>(() => {
  if (route.name === 'strategies' || route.name === 'strategy-cross-section') return 'strategy'
  if (route.name === 'indicators' || route.name === 'indicator-cross-section') return 'indicator'
  return props.lensType
})
const isIndicator = computed(() => activeLensType.value === 'indicator')
const pageTitle = computed(() => isIndicator.value ? '指标横向扫描' : '策略横向扫描')
const pageKicker = computed(() => isIndicator.value ? 'INDICATOR LENS / OBSERVATION RUN' : 'STRATEGY LENS / OBSERVATION RUN')
const pageDescription = computed(() => isIndicator.value
  ? '从指标事实快速比较状态、变化和观察组证据。'
  : '从策略事实快速比较方向、阶段和变化。')
const crossSectionAiSource = computed<RemoteDecisionSource>(() => ({
  observation_run_id: selectedRunId.value || undefined,
  lens_id: selectedLensId.value || undefined,
  lens_type: projection.value?.lens.type,
  lens_version: projection.value?.lens.version ?? undefined,
  content_sha256: projection.value?.content_sha256,
}))
const crossSectionAiDisabled = computed(() => !projection.value || !selectedRunId.value || !selectedLensId.value)
const activeItem = computed(() => catalog.value.find((item) => item.id === selectedLensId.value) ?? null)
const selectedRun = computed(() => runs.value.find((run) => run.run_id === selectedRunId.value) ?? null)
const readyRuns = computed(() => runs.value.filter((run) => run.status === 'succeeded' || run.status === 'mixed'))
const latestReadyRun = computed(() => readyRuns.value[0] ?? null)
const automaticFallbackRun = computed(() => runs.value.find((run) => run.run_id === automaticFallbackRunId.value) ?? null)
const comparison = computed<CrossSectionComparison>(() => projection.value?.comparison ?? {
  mode: 'previous_trading_session',
  status: 'unavailable',
  current_trading_date: projection.value?.trading_date ?? selectedRun.value?.trading_date ?? '—',
  previous_trading_date: null,
  previous_trading_dates: [],
  available_group_count: 0,
  group_count: projection.value?.quality.projected_group_count ?? 0,
  previous_snapshot_ids: [],
  previous_dataset_ids: [],
})
const visibleGroups = computed(() => (projection.value?.groups ?? []).filter((group) => !isLegacySelfSelectedGroup(group)))
const visibleRows = computed(() => {
  const groupIds = new Set(visibleGroups.value.map((group) => group.group_id))
  return (projection.value?.rows ?? []).filter((row) => groupIds.has(row.group_id))
})

type FocusFilter = 'all' | 'focused' | 'unfocused'
type FocusState = 'loading' | 'available' | 'failed'
type IndicatorSortKey = 'new_changes' | 'change' | 'value' | 'symbol'
type StrategySortKey = 'stage_change' | 'stage' | 'score' | 'symbol'
type SortKey = IndicatorSortKey | StrategySortKey

interface ComparisonTableRow extends CrossSectionRow {
  table_key: string
  group_names: string[]
  group_ids: string[]
  benchmark_key: string
}

const searchQuery = ref('')
const focusFilter = ref<FocusFilter>('all')
const focusState = ref<FocusState>('loading')
const focusError = ref('')
const focusSymbols = ref<Set<string>>(new Set())
const groupFilter = ref('')
const stateFilter = ref('')
const sortKey = ref<SortKey>('new_changes')
const sortDirection = ref<'asc' | 'desc'>('desc')
let focusRequestSequence = 0

const focusFilterAvailable = computed(() => focusState.value === 'available')
const stateLabels: Record<string, string> = {
  oversold: '超卖', overbought: '超买', balanced: '平衡', positive: '正值', negative: '负值', zero: '零轴',
  histogram_rising: 'MACD 柱值上升（ΔH > 0）', histogram_falling: 'MACD 柱值下降（ΔH < 0）', histogram_flat: 'MACD 柱值持平（ΔH = 0）',
  slope_increasing: 'MACD 柱值斜率向上变化（Δ²H > 0）', slope_decreasing: 'MACD 柱值斜率向下变化（Δ²H < 0）', slope_unchanged: 'MACD 柱值斜率变化为零（Δ²H = 0）',
  not_computed: '未计算此版本', insufficient_sample: '样本不足',
  leading: '相对领先', lagging: '相对落后', inline: '接近基准', expanding: '放量', contracting: '缩量', normal: '正常', flat: '持平',
  above: '线上', below: '线下', missing: '缺失', forming: '形成中', near_confirmation: '接近确认', watching: '观察中', armed: '待确认',
  confirmed: '已确认', invalidated: '已失效', no_setup: '无形态', ineligible: '不适用', insufficient_data: '数据不足',
  bullish: '偏多', bearish: '偏空', neutral: '中性',
}
const actionLabels: Record<string, string> = { prioritize: '优先关注', watch: '观察', avoid: '回避', wait: '等待', no_action: '不行动' }
const lensNameLabels: Record<string, string> = {
  rsi14: '相对强弱指标 RSI 14', macd_histogram: 'MACD 柱体', macd_histogram_delta: 'MACD 柱值变化 ΔH', macd_histogram_delta2: 'MACD 柱值变化的变化 Δ²H', relative_strength_20d: '20 日相对强弱', volume_ratio_20d: '20 日成交量比',
  return_20d: '20 日收益率', above_ma20: '站上 20 日均线', above_ma50: '站上 50 日均线', above_ma200: '站上 200 日均线',
  trend_momentum_v1: '趋势动量策略', mean_reversion_v1: '均值回归策略', breakout_volume_v1: '突破量能策略',
  relative_strength_rotation_v1: '相对强弱轮动策略', quality_left_side_reversal_v1: '优质资产左侧反转策略',
}
const strategyStageOrder: Record<string, number> = { confirmed: 6, near_confirmation: 5, armed: 4, watching: 3, forming: 2, invalidated: 1, insufficient_data: 0 }
const activeLensId = computed(() => projection.value?.lens.id ?? selectedLensId.value)
const isRelativeStrength = computed(() => activeLensId.value === 'relative_strength_20d')
const isRawMacdMomentum = computed(() => ['macd_histogram_delta', 'macd_histogram_delta2'].includes(activeLensId.value))
const sortOptions = computed(() => isIndicator.value
  ? isRawMacdMomentum.value
    ? [{ value: 'new_changes', label: '状态变化优先' }, { value: 'symbol', label: 'symbol' }]
    : [
        { value: 'new_changes', label: '新变化优先' }, { value: 'change', label: '变化幅度' }, { value: 'value', label: '当前值' }, { value: 'symbol', label: 'symbol' },
      ]
  : [
      { value: 'stage_change', label: '阶段变化优先' }, { value: 'stage', label: '阶段' }, { value: 'score', label: 'score' }, { value: 'symbol', label: 'symbol' },
    ])
const sortLabel = computed(() => sortOptions.value.find((option) => option.value === sortKey.value)?.label ?? '—')
const lensDisplayName = computed(() => lensNameLabels[activeLensId.value] ?? activeItem.value?.name ?? (isIndicator.value ? projection.value?.indicator?.name : projection.value?.strategy?.name) ?? activeLensId.value)
const lensImplementationLabel = computed(() => `${activeLensId.value || '—'} · ${formatLensVersion(projection.value?.lens.version ?? activeItem.value?.version)}${isRawMacdMomentum.value ? ' · 原始价格尺度，不跨标的比较' : ''}`)

function formatLensVersion(value: string | null | undefined) {
  const version = value?.trim()
  if (!version) return '—'
  if (/^v?\d+(?:\.\d+){0,2}$/.test(version)) return version.startsWith('v') ? version : `v${version}`
  return version
}

function normalizeSymbol(value: string) {
  return value.trim().toUpperCase()
}

function isFocused(symbol: string) {
  return focusSymbols.value.has(normalizeSymbol(symbol))
}

async function loadFocusSymbols() {
  const requestId = ++focusRequestSequence
  focusState.value = 'loading'
  focusError.value = ''
  try {
    const symbols = await api.listFocusSymbols()
    if (requestId !== focusRequestSequence) return
    focusSymbols.value = new Set(symbols.map((item) => normalizeSymbol(item.symbol)).filter(Boolean))
    focusState.value = 'available'
  } catch (reason) {
    if (requestId !== focusRequestSequence) return
    focusState.value = 'failed'
    focusFilter.value = 'all'
    focusError.value = reason instanceof Error ? reason.message : '关注状态未知'
  }
}

function retryFocusSymbols() {
  void loadFocusSymbols()
}

function rawGroupNameOf(group: CrossSectionProjection['groups'][number]) {
  return group.group_name || group.display_name || group.group_id
}

function groupNameOf(group: CrossSectionProjection['groups'][number]) {
  const rawName = rawGroupNameOf(group)
  if (group.group_id === 'universe-core-watchlist' || rawName === '核心关注列表' || rawName === '指标推荐') return '指标推荐'
  return rawName
}

function isLegacySelfSelectedGroup(group: CrossSectionProjection['groups'][number]) {
  return ['核心观察组', '自选组', '自选个股'].includes(rawGroupNameOf(group))
}

function tableGroupNameOf(row: CrossSectionRow) {
  const group = visibleGroups.value.find((item) => item.group_id === row.group_id)
  return group ? groupNameOf(group) : row.group_name || row.group_id
}

function benchmarkKeyOf(row: CrossSectionRow) {
  if (!isRelativeStrength.value) return ''
  return [...new Set((row.benchmark_symbols ?? []).map(normalizeSymbol))].sort().join(',') || '—'
}

const tableRows = computed<ComparisonTableRow[]>(() => {
  const deduped = new Map<string, ComparisonTableRow>()
  for (const row of visibleRows.value) {
    const benchmarkKey = benchmarkKeyOf(row)
    const tableKey = [row.symbol, activeLensId.value, row.dataset_id, benchmarkKey].join('|')
    const groupName = tableGroupNameOf(row)
    const existing = deduped.get(tableKey)
    if (existing) {
      existing.group_names = [...new Set([...existing.group_names, groupName])].sort((left, right) => left.localeCompare(right, 'zh-Hans'))
      existing.group_ids = [...new Set([...existing.group_ids, row.group_id])].sort((left, right) => left.localeCompare(right, 'en'))
      continue
    }
    deduped.set(tableKey, { ...row, table_key: tableKey, group_names: [groupName], group_ids: [row.group_id], benchmark_key: benchmarkKey })
  }
  return [...deduped.values()]
})
function stateFilterToken(row: CrossSectionRow) {
  if (row.valid) return String(row.state ?? 'missing')
  if (row.state_label === '未计算此版本') return 'not_computed'
  if (row.state_label === '样本不足') return 'insufficient_sample'
  return 'missing'
}
const stateFilterOptions = computed(() => {
  const options = new Map<string, string>()
  for (const row of tableRows.value) {
    const token = stateFilterToken(row)
    const label = row.valid ? row.state_label ?? stateLabels[token] ?? token : row.state_label ?? stateLabels[token] ?? '数据缺失'
    options.set(token, label)
  }
  return [...options.entries()].map(([value, label]) => ({ value, label }))
})
const uniqueValidRowCount = computed(() => tableRows.value.filter((row) => row.valid).length)

function tableStageOf(row: CrossSectionRow) {
  return String(row.setup_progress?.stage ?? row.state ?? 'insufficient_data')
}

function changeMagnitudeOf(row: CrossSectionRow) {
  return row.change === null || row.change === undefined || !Number.isFinite(row.change) ? Number.NEGATIVE_INFINITY : Math.abs(row.change)
}

function compareNumber(left: number, right: number, direction: 'asc' | 'desc') {
  if (left === right) return 0
  const result = left < right ? -1 : 1
  return direction === 'asc' ? result : -result
}

function compareTableRows(left: ComparisonTableRow, right: ComparisonTableRow) {
  if (sortKey.value === 'symbol') return left.symbol.localeCompare(right.symbol, 'en') * (sortDirection.value === 'asc' ? 1 : -1)
  const isChangePrioritySort = sortKey.value === 'new_changes' || sortKey.value === 'stage_change'
  const leftPrimary = isChangePrioritySort
    ? (left.transition ? 1 : 0)
    : sortKey.value === 'change'
      ? changeMagnitudeOf(left)
      : sortKey.value === 'value'
        ? left.value ?? Number.NEGATIVE_INFINITY
        : sortKey.value === 'score'
          ? left.score ?? Number.NEGATIVE_INFINITY
          : strategyStageOrder[tableStageOf(left)] ?? -1
  const rightPrimary = isChangePrioritySort
    ? (right.transition ? 1 : 0)
    : sortKey.value === 'change'
      ? changeMagnitudeOf(right)
      : sortKey.value === 'value'
        ? right.value ?? Number.NEGATIVE_INFINITY
        : sortKey.value === 'score'
          ? right.score ?? Number.NEGATIVE_INFINITY
          : strategyStageOrder[tableStageOf(right)] ?? -1
  const primaryResult = compareNumber(leftPrimary, rightPrimary, sortDirection.value)
  if (primaryResult) return primaryResult
  if (isChangePrioritySort) {
    const magnitudeResult = compareNumber(changeMagnitudeOf(left), changeMagnitudeOf(right), 'desc')
    if (magnitudeResult) return magnitudeResult
  }
  return left.symbol.localeCompare(right.symbol, 'en') || left.table_key.localeCompare(right.table_key, 'en')
}

const sortedTableRows = computed(() => [...tableRows.value].sort(compareTableRows))
const filteredTableRows = computed(() => {
  const query = searchQuery.value.trim().toLocaleLowerCase()
  return sortedTableRows.value.filter((row) => {
    if (focusFilter.value === 'focused' && focusFilterAvailable.value && !isFocused(row.symbol)) return false
    if (focusFilter.value === 'unfocused' && focusFilterAvailable.value && isFocused(row.symbol)) return false
    if (groupFilter.value && !row.group_ids.includes(groupFilter.value)) return false
    if (stateFilter.value && stateFilterToken(row) !== stateFilter.value) return false
    if (!query) return true
    return row.symbol.toLocaleLowerCase().includes(query) || row.group_names.some((name) => name.toLocaleLowerCase().includes(query))
  })
})

type VisualKind = 'rsi' | 'volume' | 'zero' | 'binary' | 'strategy' | 'categorical'
const visualKind = computed<VisualKind>(() => {
  if (!isIndicator.value) return 'strategy'
  if (isRawMacdMomentum.value) return 'categorical'
  if (activeLensId.value === 'rsi14') return 'rsi'
  if (activeLensId.value === 'volume_ratio_20d') return 'volume'
  if (activeLensId.value.startsWith('above_ma')) return 'binary'
  return 'zero'
})
const visualScale = computed(() => {
  if (visualKind.value === 'categorical') return { min: 0, max: 1 }
  if (visualKind.value === 'rsi') return { min: 0, max: 100 }
  if (visualKind.value === 'binary') return { min: 0, max: 1 }
  if (visualKind.value === 'strategy') return { min: -100, max: 100 }
  const values = tableRows.value.flatMap((row) => [row.value, row.previous_value]).filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
  if (visualKind.value === 'volume') return { min: 0, max: Math.max(1.5, ...values) }
  const bound = Math.max(1, ...values.map(Math.abs))
  return { min: -bound, max: bound }
})
function visualZonesFor(row: CrossSectionRow) {
  if (visualKind.value === 'rsi') {
    const thresholds = row.thresholds ?? activeItem.value?.thresholds ?? projection.value?.indicator?.thresholds ?? {}
    const low = Math.min(100, Math.max(0, Number(thresholds.oversold ?? 30)))
    const high = Math.min(100, Math.max(low, Number(thresholds.overbought ?? 70)))
    return [{ label: '超卖', tone: 'attention', from: 0, to: low }, { label: '平衡', tone: 'neutral', from: low, to: high }, { label: '超买', tone: 'negative', from: high, to: 100 }]
  }
  if (visualKind.value === 'volume') {
    const thresholds = row.thresholds ?? activeItem.value?.thresholds ?? projection.value?.indicator?.thresholds ?? {}
    const low = Number(thresholds.contraction ?? thresholds.contracting ?? 0.8)
    const high = Number(thresholds.expansion ?? thresholds.expanding ?? 1.2)
    return [{ label: '缩量', tone: 'negative', from: 0, to: low }, { label: '正常', tone: 'neutral', from: low, to: high }, { label: '放量', tone: 'positive', from: high, to: visualScale.value.max }]
  }
  if (visualKind.value === 'strategy') return [{ label: '偏空', tone: 'negative', from: -100, to: -25 }, { label: '中性', tone: 'neutral', from: -25, to: 25 }, { label: '偏多', tone: 'positive', from: 25, to: 100 }]
  if (visualKind.value === 'binary') return [{ label: '线下', tone: 'negative', from: 0, to: .5 }, { label: '线上', tone: 'positive', from: .5, to: 1 }]
  return [{ label: '负值', tone: 'negative', from: visualScale.value.min, to: 0 }, { label: '正值', tone: 'positive', from: 0, to: visualScale.value.max }]
}
function visualTicksFor(row: CrossSectionRow) {
  if (visualKind.value === 'rsi' || visualKind.value === 'volume') return visualZonesFor(row).slice(0, 2).map((zone) => zone.to)
  if (visualKind.value === 'strategy') return [-25, 0, 25]
  return [0]
}

function plotLabel() {
  if (visualKind.value === 'categorical') return '状态，不比较原始数值尺度'
  if (visualKind.value === 'rsi') return 'RSI 区间'
  if (visualKind.value === 'volume') return '成交量比区间'
  if (visualKind.value === 'strategy') return '策略方向 / SCORE'
  if (visualKind.value === 'binary') return '均线位置'
  return '零轴位置'
}

function scaleLabel(value: number) {
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(2)))
}
const stateDistribution = computed(() => {
  const counts = new Map<string, { label: string; tone: string; count: number }>()
  for (const row of filteredTableRows.value) {
    const key = row.valid ? (isIndicator.value ? row.state : row.stance ?? 'neutral') : stateFilterToken(row)
    const current = counts.get(key)
    if (current) current.count += 1
    else counts.set(key, { label: row.valid ? stateLabels[key] ?? key : row.state_label ?? stateLabels[key] ?? '数据缺失', tone: row.valid ? toneOf(row) : 'attention', count: 1 })
  }
  return [...counts.values()].sort((a, b) => b.count - a.count)
})
const changedRowCount = computed(() => filteredTableRows.value.filter((row) => row.transition).length)
const showTable = ref(false)

function visualPosition(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) return null
  const { min, max } = visualScale.value
  return Number(Math.max(0, Math.min(100, (value - min) / (max - min) * 100)).toFixed(2))
}

function visualZoneStyle(from: number, to: number) {
  const start = visualPosition(from) ?? 0
  const end = visualPosition(to) ?? 100
  return { left: `${start}%`, width: `${Math.max(0, end - start)}%` }
}

function visualMarkerStyle(value: number | null | undefined) {
  const position = visualPosition(value)
  return position === null ? {} : { left: `${position}%` }
}

function zeroBarStyle(value: number | null | undefined) {
  const position = visualPosition(value)
  const zero = visualPosition(0) ?? 50
  return position === null ? {} : { left: `${Math.min(position, zero)}%`, width: `${Math.abs(position - zero)}%` }
}

function ratioFillStyle(value: number | null | undefined) {
  const position = visualPosition(value)
  return position === null ? {} : { width: `${position}%` }
}

const strategyStages = ['forming', 'watching', 'armed', 'near_confirmation', 'confirmed'] as const
function strategyStageIndex(row: CrossSectionRow) {
  return strategyStages.findIndex((stage) => stage === tableStageOf(row))
}

function visualValue(row: CrossSectionRow) {
  return isIndicator.value ? row.value : row.score
}

const discoveryEventId = computed(() => typeof route.query.discoveryEvent === 'string' ? route.query.discoveryEvent : '')
const discoveryLensId = computed(() => typeof route.query.lens === 'string' ? route.query.lens : '')
const discoverySymbol = computed(() => typeof route.query.symbol === 'string' ? normalizeSymbol(route.query.symbol) : '')
const discoveryTargetSymbol = computed(() => {
  if (discoverySymbol.value) return discoverySymbol.value
  const transition = projection.value?.transitions.find((item) => item.id === discoveryEventId.value)
  return transition?.symbol ? normalizeSymbol(transition.symbol) : ''
})
const highlightedTableKeys = computed(() => {
  if (!discoveryEventId.value && !discoveryTargetSymbol.value) return []
  if (discoveryLensId.value && discoveryLensId.value !== activeLensId.value) return []
  return tableRows.value.filter((row) => {
    if (discoveryTargetSymbol.value && normalizeSymbol(row.symbol) !== discoveryTargetSymbol.value) return false
    if (!discoveryTargetSymbol.value && discoveryEventId.value && row.id !== discoveryEventId.value) return false
    return true
  }).map((row) => row.table_key)
})
const discoveryContextLabel = computed(() => {
  if (!discoveryEventId.value && !discoveryTargetSymbol.value) return ''
  return `已定位 ${discoveryTargetSymbol.value || '变化事件'}${discoveryEventId.value ? ` · ${discoveryEventId.value}` : ''}`
})

function toggleSortDirection() {
  sortDirection.value = sortDirection.value === 'asc' ? 'desc' : 'asc'
}

function transitionText(row: CrossSectionRow) {
  if (!row.transition) return previousValueOf(row)
  return `${stateLabels[row.transition.from] ?? row.transition.from} → ${stateLabels[row.transition.to] ?? row.transition.to}`
}

function transitionCaption(row: CrossSectionRow) {
  if (row.transition) return '状态变化'
  return row.previous_value === null || row.previous_value === undefined ? '无可比数据' : '前一交易日'
}

function tableQualityLabel(row: CrossSectionRow) {
  if (!row.valid || row.status === 'missing') return row.state_label === '未计算此版本' || row.state_label === '样本不足' ? row.state_label : '数据缺失'
  return row.quality_status || row.status || '—'
}

function currentStateLabel(row: CrossSectionRow) {
  return isIndicator.value ? statusOf(row) : `${stateOf(row)} · ${actionOf(row)}`
}

function currentStageOrValueLabel(row: CrossSectionRow) {
  return isIndicator.value ? valueOf(row) : stageOf(row)
}

function tableCurrentSecondary(row: CrossSectionRow) {
  return isIndicator.value ? row.unit ?? 'current value' : `score ${scoreOf(row)}`
}

function updateSortDefaults(lensType: CrossSectionLensType) {
  sortKey.value = lensType === 'indicator' ? 'new_changes' : 'stage_change'
  sortDirection.value = 'desc'
}

function routeLensId(lensType: CrossSectionLensType = activeLensType.value) {
  const key = lensType === 'indicator' ? 'indicatorId' : 'strategyId'
  const value = route.params[key]
  return typeof value === 'string' ? value : ''
}

function routeName(lensType: CrossSectionLensType = activeLensType.value) {
  return lensType === 'indicator' ? 'indicator-cross-section' : 'strategy-cross-section'
}

function indexRouteName(lensType: CrossSectionLensType = activeLensType.value) {
  return lensType === 'indicator' ? 'indicators' : 'strategies'
}

function isReadyRun(run: ObservationRunSummary) {
  return run.status === 'succeeded' || run.status === 'mixed'
}

function projectionValidRowCount(value: CrossSectionProjection) {
  return typeof value.quality?.valid_row_count === 'number' ? value.quality.valid_row_count : value.rows.filter((row) => row.valid).length
}

function isCurrentRequest(requestId: number, lensType: CrossSectionLensType, runId: string, lensId: string) {
  return requestId === requestSequence && activeLensType.value === lensType && selectedRunId.value === runId && selectedLensId.value === lensId
}

function shortHash(value: string | null | undefined) {
  return value ? `${value.slice(0, 10)}…` : '—'
}

function formatChange(row: CrossSectionRow) {
  if (row.change === null || row.change === undefined) return '—'
  const digits = row.unit === 'ratio' ? 2 : row.unit === 'boolean' ? 0 : 2
  return `${row.change >= 0 ? '+' : ''}${row.change.toFixed(digits)}${row.unit === 'percent' ? '%' : ''}`
}

function comparisonDateLabel() {
  if (comparison.value.previous_trading_date) return comparison.value.previous_trading_date
  if (comparison.value.previous_trading_dates.length > 1) return '各组不同'
  return '无可比数据'
}

function comparisonStatusLabel() {
  if (comparison.value.status === 'ok') return '已对比'
  if (comparison.value.status === 'partial') return '部分可比'
  return '首次运行'
}

function previousValueOf(row: CrossSectionRow) {
  if (row.previous_value === null || row.previous_value === undefined) return '—'
  if (!isIndicator.value) return row.previous_value.toFixed(0)
  return row.previous_display_value ?? row.previous_value.toFixed(2)
}

function valueOf(row: CrossSectionRow) {
  if (!isIndicator.value) return row.score === null || row.score === undefined ? '—' : row.score.toFixed(0)
  return row.display_value
}

function actionOf(row: CrossSectionRow) {
  return actionLabels[row.action ?? ''] ?? row.action ?? '—'
}

function stageOf(row: CrossSectionRow) {
  const stage = row.setup_progress?.stage ?? row.state
  return stateLabels[String(stage)] ?? String(stage ?? '—')
}

function scoreOf(row: CrossSectionRow) {
  return row.score === null || row.score === undefined ? '—' : row.score.toFixed(0)
}

function stateOf(row: CrossSectionRow) {
  if (!isIndicator.value) return stateLabels[row.stance ?? ''] ?? row.stance ?? '—'
  return stateLabels[row.state] ?? row.state
}

function toneOf(row: CrossSectionRow) {
  if (!isIndicator.value) {
    if (row.stance === 'bullish') return 'positive'
    if (row.stance === 'bearish') return 'negative'
    if (row.stance === 'insufficient_data' || !row.valid) return 'attention'
    return 'neutral'
  }
  if (['positive', 'leading', 'above', 'expanding', 'confirmed'].includes(row.state)) return 'positive'
  if (['negative', 'lagging', 'below', 'contracting', 'overbought', 'invalidated'].includes(row.state)) return 'negative'
  if (['oversold', 'armed', 'watching'].includes(row.state)) return 'attention'
  return 'neutral'
}

function instrumentLink(symbol: string, datasetId: string) {
  return { name: 'instrument-decision', params: { symbol }, query: { dataset: datasetId, ...(projection.value?.observation_run_id ? { run: projection.value.observation_run_id } : {}) } }
}

function statusOf(row: CrossSectionRow) {
  if (row.status === 'missing' || !row.valid) return row.state_label === '未计算此版本' || row.state_label === '样本不足' ? row.state_label : '数据缺失'
  return stateOf(row)
}

async function loadProjection(runId = selectedRunId.value, lensId = selectedLensId.value, lensType: CrossSectionLensType = activeLensType.value, requestId = ++requestSequence) {
  if (requestId === requestSequence) {
    projection.value = null
    error.value = ''
  }
  if (!runId || !lensId) {
    if (requestId === requestSequence) loadingProjection.value = false
    return null
  }
  loadingProjection.value = true
  try {
    const next = lensType === 'indicator' ? await api.getIndicatorCrossSection(runId, lensId) : await api.getStrategyCrossSection(runId, lensId)
    if (!isCurrentRequest(requestId, lensType, runId, lensId)) return null
    projection.value = next
    return next
  } catch (reason) {
    if (isCurrentRequest(requestId, lensType, runId, lensId)) error.value = reason instanceof Error ? reason.message : '横向投影加载失败。'
    return null
  } finally {
    if (requestId === requestSequence) loadingProjection.value = false
  }
}

async function selectLens(id: string) {
  if (id === selectedLensId.value) return
  const lensType = activeLensType.value
  const requestId = ++requestSequence
  selectedLensId.value = id
  groupFilter.value = ''
  automaticFallbackRunId.value = ''
  projection.value = null
  await router.push({ name: routeName(lensType), params: { [lensType === 'indicator' ? 'indicatorId' : 'strategyId']: id }, query: selectedRunId.value ? { run: selectedRunId.value } : undefined })
  await loadProjection(selectedRunId.value, id, lensType, requestId)
}

async function selectRun() {
  const lensType = activeLensType.value
  const requestId = ++requestSequence
  if (!readyRuns.value.some((run) => run.run_id === selectedRunId.value)) selectedRunId.value = readyRuns.value[0]?.run_id || ''
  automaticFallbackRunId.value = ''
  projection.value = null
  await router.replace({ name: routeLensId(lensType) ? routeName(lensType) : indexRouteName(lensType), params: route.params, query: selectedRunId.value ? { run: selectedRunId.value } : undefined })
  await loadProjection(selectedRunId.value, selectedLensId.value, lensType, requestId)
}

async function loadPage(lensType: CrossSectionLensType = activeLensType.value) {
  const requestId = ++requestSequence
  pageReady.value = false
  automaticFallbackRunId.value = ''
  loading.value = true
  error.value = ''
  projection.value = null
  catalog.value = []
  runs.value = []
  selectedLensId.value = ''
  selectedRunId.value = ''
  try {
    const [items, runItems] = await Promise.all([lensType === 'indicator' ? api.listIndicatorCatalog() : api.listStrategyCatalog(), api.listObservationRunSummaries(30)])
    if (requestId !== requestSequence || activeLensType.value !== lensType) return
    catalog.value = items
    runs.value = runItems
    const requestedLensId = routeLensId(lensType)
    selectedLensId.value = items.some((item) => item.id === requestedLensId) ? requestedLensId : items[0]?.id || ''
    const queryRun = typeof route.query.run === 'string' ? route.query.run : ''
    const requestedRun = runItems.find((run) => run.run_id === queryRun && isReadyRun(run))
    const candidates = requestedRun ? [requestedRun] : runItems.filter(isReadyRun)
    const firstCandidateId = candidates[0]?.run_id
    for (const candidate of candidates) {
      if (requestId !== requestSequence || activeLensType.value !== lensType) return
      selectedRunId.value = candidate.run_id
      const next = await loadProjection(candidate.run_id, selectedLensId.value, lensType, requestId)
      if (requestId !== requestSequence || activeLensType.value !== lensType) return
      if (!next) continue
      const hasUsableEvidence = projectionValidRowCount(next) > 0
      if (requestedRun || hasUsableEvidence || candidate.run_id === candidates[candidates.length - 1]?.run_id) {
        if (!requestedRun && firstCandidateId && candidate.run_id !== firstCandidateId && hasUsableEvidence) automaticFallbackRunId.value = candidate.run_id
        break
      }
    }
  } catch (reason) {
    if (requestId === requestSequence && activeLensType.value === lensType) error.value = reason instanceof Error ? reason.message : '扫描目录加载失败。'
  } finally {
    if (requestId === requestSequence && activeLensType.value === lensType) {
      pageReady.value = true
      loading.value = false
    }
  }
}

watch(activeLensType, (lensType, previousLensType) => {
  updateSortDefaults(lensType)
  if (previousLensType !== undefined && lensType !== previousLensType) void loadPage(lensType)
}, { immediate: true })

watch(activeLensId, () => {
  stateFilter.value = ''
  if (isRawMacdMomentum.value && (sortKey.value === 'value' || sortKey.value === 'change')) {
    sortKey.value = 'new_changes'
  }
})

watch(
  () => [activeLensType.value, route.name, route.params.indicatorId, route.params.strategyId, route.query.run],
  (value, previousValue) => {
    if (!pageReady.value) return
    if (previousValue && value[0] !== previousValue[0]) return
    const lensType = activeLensType.value
    const lensId = routeLensId(lensType)
    const routeHasValidLens = catalog.value.some((item) => item.id === lensId)
    const lensChanged = Boolean(lensId && routeHasValidLens && lensId !== selectedLensId.value)
    const queryRun = typeof route.query.run === 'string' ? route.query.run : ''
    const runChanged = Boolean(queryRun && queryRun !== selectedRunId.value && readyRuns.value.some((run) => run.run_id === queryRun))
    const indexChanged = route.name === indexRouteName(lensType) && !lensId
    if (indexChanged) {
      void loadPage()
      return
    }
    if (!lensChanged && !runChanged) return
    if (lensChanged) selectedLensId.value = lensId
    if (runChanged) selectedRunId.value = queryRun
    automaticFallbackRunId.value = ''
    void loadProjection(selectedRunId.value, selectedLensId.value, lensType, ++requestSequence)
  },
)

onMounted(() => {
  void loadPage()
  void loadFocusSymbols()
})
</script>

<template>
  <AppShell />
  <main class="cross-section-page">
    <section class="cross-section-hero">
      <div>
        <p class="eyebrow">{{ pageKicker }}</p>
        <h1>{{ pageTitle }}</h1>
        <p>{{ pageDescription }}</p>
      </div>
      <div class="cross-section-hero-actions">
        <label class="cross-section-run-picker">
          <span>OBSERVATION RUN</span>
          <select v-model="selectedRunId" :disabled="!readyRuns.length" @change="selectRun">
            <option v-if="!readyRuns.length" value="">暂无已完成观察</option>
            <option v-for="run in readyRuns" :key="run.run_id" :value="run.run_id">
              {{ run.trading_date }} · {{ run.status }}{{ run.quality_issue_count ? ` · ${run.quality_issue_count} quality issues` : '' }}
            </option>
          </select>
        </label>
        <RemoteDecisionPanel
          :intent-type="isIndicator ? 'indicator_attention' : 'strategy_attention'"
          :label="isIndicator ? 'AI 找指标关注项' : 'AI 找策略关注项'"
          :title="isIndicator ? '确认指标横截面 AI 扫描' : '确认策略横截面 AI 扫描'"
          :source="crossSectionAiSource"
          :disabled="crossSectionAiDisabled"
          trigger-class="cross-section-ai-button"
          preflight-on-mount
          compact
        />
      </div>
    </section>

    <div v-if="error" class="error-banner" role="alert">{{ error }}</div>
    <div v-if="automaticFallbackRun && latestReadyRun" class="cross-section-warning" role="status">
      最新 Observation Run（{{ latestReadyRun.trading_date }}）没有有效横向证据，已自动切换到 {{ automaticFallbackRun.trading_date }} 的可用冻结 Run。
    </div>

    <section v-if="loading" class="empty-panel cross-section-empty"><p>正在读取横向扫描目录…</p></section>
    <section v-else-if="!readyRuns.length" class="empty-panel cross-section-empty">
      <p class="eyebrow">NO FROZEN OBSERVATION</p>
      <h2>还没有已完成的 Observation Run。</h2>
      <p>当前列表中的 Run 仍在执行或已失败；完成一次收市观察后，指标和策略页面会共享同一批冻结组快照。</p>
      <RouterLink class="primary-button" to="/observation-runs">前往收市观察</RouterLink>
    </section>
    <template v-else>
      <section class="cross-section-lens-strip">
        <div class="cross-section-lens-tabs" role="tablist" :aria-label="pageTitle">
          <button
            v-for="item in catalog"
            :key="item.id"
            class="cross-section-lens-tab"
            :class="{ active: item.id === selectedLensId }"
            type="button"
            role="tab"
            :aria-selected="item.id === selectedLensId"
            @click="selectLens(item.id)"
          >
            <strong>{{ lensNameLabels[item.id] ?? item.name }}</strong>
            <small>{{ item.id }} · {{ formatLensVersion(item.version ?? item.feature_version) }}</small>
          </button>
        </div>
      </section>

      <section v-if="loadingProjection" class="empty-panel cross-section-empty"><p>正在读取冻结投影…</p></section>
      <template v-else-if="projection">
        <section class="cross-section-evidence-strip" aria-label="冻结证据摘要">
          <div><span>AS OF</span><strong>{{ projection.trading_date }}</strong><small>收市冻结快照</small></div>
          <div><span>COMPARE</span><strong>{{ comparisonStatusLabel() }}</strong><small>前一交易日 {{ comparisonDateLabel() }} · {{ comparison.available_group_count }} / {{ comparison.group_count }} 组</small></div>
          <div><span>QUALITY</span><strong :data-tone="projection.quality.status === 'ok' ? 'positive' : 'attention'">{{ projection.quality.status }}</strong><small>{{ uniqueValidRowCount }} / {{ tableRows.length }} unique facts valid</small></div>
          <div><span>EVIDENCE</span><strong>{{ shortHash(projection.content_sha256) }}</strong><small>RUN {{ shortHash(projection.observation_run_id) }} · {{ projection.quality.dataset_ids.length }} datasets</small></div>
        </section>

        <section class="cross-section-workspace-heading">
          <div>
            <p class="section-kicker">{{ isIndicator ? 'INDICATOR COMPARISON' : 'STRATEGY COMPARISON' }}</p>
            <h2>{{ lensDisplayName }}</h2>
            <small class="cross-section-lens-identity">{{ lensImplementationLabel }}</small>
            <p v-if="discoveryContextLabel" class="cross-section-discovery-context" data-testid="discovery-context">{{ discoveryContextLabel }}</p>
          </div>
          <p class="cross-section-workspace-note">{{ isIndicator ? '颜色表示状态，不是交易指令。' : '阶段、方向与 score 分开显示，不代表概率。' }}</p>
        </section>

        <section class="cross-section-table-toolbar" data-testid="cross-section-toolbar" aria-label="横向比较筛选和排序">
          <label class="cross-section-control cross-section-search-control">
            <span>搜索</span>
            <input v-model="searchQuery" data-testid="cross-section-search" type="search" placeholder="symbol / 组名" />
          </label>
          <label class="cross-section-control">
            <span>重点筛选</span>
            <select v-model="focusFilter" data-testid="cross-section-focus-filter">
              <option value="all">全部</option>
              <option value="focused" :disabled="!focusFilterAvailable">重点关注</option>
              <option value="unfocused" :disabled="!focusFilterAvailable">非重点关注</option>
            </select>
          </label>
          <label class="cross-section-control">
            <span>观察组</span>
            <select v-model="groupFilter" data-testid="cross-section-group-filter">
              <option value="">全部组</option>
              <option v-for="group in visibleGroups" :key="group.group_id" :value="group.group_id">{{ groupNameOf(group) }}</option>
            </select>
          </label>
          <label v-if="isIndicator" class="cross-section-control">
            <span>指标状态</span>
            <select v-model="stateFilter" data-testid="cross-section-state-filter">
              <option value="">全部状态</option>
              <option v-for="option in stateFilterOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
            </select>
          </label>
          <label class="cross-section-control">
            <span>排序</span>
            <select v-model="sortKey" data-testid="cross-section-sort">
              <option v-for="option in sortOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
            </select>
          </label>
          <button class="cross-section-sort-direction" data-testid="cross-section-sort-direction" type="button" @click="toggleSortDirection">
            {{ sortDirection === 'asc' ? '升序' : '降序' }}
          </button>
          <p v-if="focusState === 'loading'" class="cross-section-focus-status" data-testid="cross-section-focus-loading" role="status">正在读取关注状态…</p>
          <p v-else-if="focusState === 'failed'" class="cross-section-focus-status" data-testid="cross-section-focus-error" role="status">
            关注状态未知
            <button type="button" data-testid="cross-section-focus-retry" @click="retryFocusSymbols">重试</button>
          </p>
          <p v-else class="cross-section-focus-status" data-testid="cross-section-focus-loaded" role="status">关注列表已加载 · {{ focusSymbols.size }} 个</p>
        </section>

        <div class="cross-section-table-summary">
          <span>显示 {{ filteredTableRows.length }} / {{ tableRows.length }} 条去重事实</span>
          <span>当前排序：{{ sortLabel }} · {{ sortDirection === 'asc' ? '升序' : '降序' }}</span>
          <span>组级摘要：{{ visibleGroups.map((group) => `${groupNameOf(group)} ${group.valid_symbol_count}/${group.symbol_count}`).join(' · ') || '—' }}</span>
        </div>

        <section class="cross-section-visual-summary" aria-label="当前筛选的状态分布">
          <div class="cross-section-visual-summary-head">
            <div><span class="section-kicker">CROSS-SECTION MAP</span><h3>状态分布与个股位置</h3></div>
            <p>{{ changedRowCount }} 项状态切换 · {{ filteredTableRows.length }} 项当前事实</p>
          </div>
          <div v-if="stateDistribution.length" class="cross-section-distribution-track" role="img" :aria-label="stateDistribution.map((item) => `${item.label} ${item.count}`).join('，')">
            <span v-for="item in stateDistribution" :key="item.label" :data-tone="item.tone" :style="{ flexGrow: item.count }" :title="`${item.label} ${item.count}`"></span>
          </div>
          <div class="cross-section-distribution-legend">
            <span v-for="item in stateDistribution" :key="item.label" :data-tone="item.tone"><i></i>{{ item.label }} <strong>{{ item.count }}</strong></span>
            <span class="cross-section-marker-key">实心为当前 · 暗标记为前日</span>
          </div>
        </section>

        <section class="cross-section-card-grid" aria-label="个股可视化卡片" data-testid="cross-section-cards">
          <RouterLink
            v-for="row in filteredTableRows"
            :key="row.table_key"
            class="cross-section-symbol-card"
            :class="{ 'is-discovery-highlight': highlightedTableKeys.includes(row.table_key), 'is-transition': !!row.transition }"
            :data-symbol="row.symbol"
            :data-tone="toneOf(row)"
            :data-benchmark="row.benchmark_key || undefined"
            :data-focused="focusState === 'available' ? (isFocused(row.symbol) ? 'true' : 'false') : 'unknown'"
            :to="instrumentLink(row.symbol, row.dataset_id)"
          >
            <div class="cross-section-card-top">
              <div><strong>{{ row.symbol }}</strong><small>{{ row.group_names.join(' / ') }}</small></div>
              <span class="cross-section-card-state" :data-tone="toneOf(row)">{{ currentStateLabel(row) }}</span>
            </div>
            <div class="cross-section-card-reading">
              <div><span>{{ isIndicator ? '当前值' : '策略 score' }}</span><strong>{{ isIndicator ? valueOf(row) : scoreOf(row) }}</strong></div>
              <div><span>{{ isIndicator ? '较前日' : '执行阶段' }}</span><strong>{{ isIndicator ? formatChange(row) : stageOf(row) }}</strong></div>
            </div>
            <div class="cross-section-card-plot" :data-visual-kind="visualKind" role="img" :aria-label="`${row.symbol} 当前${isIndicator ? valueOf(row) : scoreOf(row)}，前日${previousValueOf(row)}；${currentStateLabel(row)}`">
              <div class="cross-section-plot-caption"><span>{{ plotLabel() }}</span><span>当前 ● <i></i> 前日</span></div>
              <template v-if="visualKind === 'categorical'">
                <div class="cross-section-categorical-state" :data-tone="toneOf(row)">{{ row.state_label || '状态不可用' }}</div>
              </template>
              <template v-else-if="visualKind === 'binary'">
                <div class="cross-section-binary-choices">
                  <span :class="{ active: row.value === 0 }" data-position="below">线下<i v-if="row.previous_value === 0" class="cross-section-binary-previous"></i></span>
                  <span :class="{ active: row.value === 1 }" data-position="above">线上<i v-if="row.previous_value === 1" class="cross-section-binary-previous"></i></span>
                </div>
                <div class="cross-section-card-scale"><span>前日 {{ previousValueOf(row) }}</span><span>均线位置 · 0 / 1</span></div>
              </template>
              <template v-else>
                <div class="cross-section-card-track">
                  <template v-if="visualKind === 'rsi'">
                    <span v-for="zone in visualZonesFor(row)" :key="zone.label" class="cross-section-card-zone" :data-tone="zone.tone" :style="visualZoneStyle(zone.from, zone.to)"></span>
                  </template>
                  <template v-else-if="visualKind === 'volume'">
                    <span v-for="zone in visualZonesFor(row)" :key="zone.label" class="cross-section-card-zone volume-zone" :data-tone="zone.tone" :style="visualZoneStyle(zone.from, zone.to)"></span>
                    <span class="cross-section-volume-fill" :data-tone="toneOf(row)" :style="ratioFillStyle(row.value)"></span>
                  </template>
                  <template v-else-if="visualKind === 'zero'">
                    <span class="cross-section-zero-line"></span>
                    <span class="cross-section-zero-bar" :data-tone="toneOf(row)" :style="zeroBarStyle(row.value)"></span>
                  </template>
                  <template v-else-if="visualKind === 'strategy'">
                    <span v-for="zone in visualZonesFor(row)" :key="zone.label" class="cross-section-card-zone strategy-zone" :data-tone="zone.tone" :style="visualZoneStyle(zone.from, zone.to)"></span>
                    <span class="cross-section-zero-line"></span>
                  </template>
                  <i v-if="row.previous_value !== null && row.previous_value !== undefined" class="cross-section-card-marker previous" :style="visualMarkerStyle(row.previous_value)"></i>
                  <i v-if="visualValue(row) !== null && visualValue(row) !== undefined" class="cross-section-card-marker current" :data-tone="toneOf(row)" :style="visualMarkerStyle(visualValue(row))"></i>
                </div>
                <div class="cross-section-card-scale">
                  <span class="cross-section-scale-start">{{ scaleLabel(visualScale.min) }}</span>
                  <span v-for="tick in visualTicksFor(row)" :key="tick" class="cross-section-scale-tick" :style="visualMarkerStyle(tick)">{{ scaleLabel(tick) }}</span>
                  <span class="cross-section-scale-end">{{ scaleLabel(visualScale.max) }}</span>
                </div>
                <div v-if="visualKind === 'strategy'" class="cross-section-stage-ladder" :aria-label="`策略阶段 ${stageOf(row)}`">
                  <span v-for="(stage, index) in strategyStages" :key="stage" :class="{ reached: strategyStageIndex(row) >= index, current: strategyStageIndex(row) === index }" :title="stateLabels[stage]"></span>
                </div>
              </template>
            </div>
            <div class="cross-section-card-bottom">
              <span>{{ row.transition ? transitionText(row) : `前日 ${previousValueOf(row)}` }}</span>
              <em>{{ row.transition ? '状态切换' : tableQualityLabel(row) }}</em>
            </div>
            <small v-if="isRelativeStrength" class="cross-section-card-benchmark">基准 {{ row.benchmark_key }}</small>
          </RouterLink>
          <div v-if="!filteredTableRows.length" class="cross-section-table-empty"><strong>{{ tableRows.length ? '没有匹配的比较事实' : '当前 Run 没有可展示的横向事实' }}</strong><span>请调整筛选条件或选择其他 Observation Run。</span></div>
        </section>

        <button class="cross-section-table-toggle" type="button" :aria-expanded="showTable" @click="showTable = !showTable">{{ showTable ? '收起明细表' : '展开完整明细表' }} <span aria-hidden="true">{{ showTable ? '↑' : '↓' }}</span></button>

        <section v-show="showTable" class="cross-section-table-shell" aria-label="横向比较表">
          <table class="cross-section-table" data-testid="cross-section-table">
            <thead>
              <tr>
                <th>symbol</th>
                <th>当前状态 / 方向</th>
                <th>{{ isIndicator ? '当前值' : '阶段 / score' }}</th>
                <th>此前</th>
                <th>变化</th>
                <th>组标签</th>
                <th>质量</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in filteredTableRows"
                :key="row.table_key"
                class="cross-section-table-row"
                :class="{ 'is-discovery-highlight': highlightedTableKeys.includes(row.table_key) }"
                :data-symbol="row.symbol"
                :data-benchmark="row.benchmark_key || undefined"
                :data-focused="focusState === 'available' ? (isFocused(row.symbol) ? 'true' : 'false') : 'unknown'"
                :data-highlighted="highlightedTableKeys.includes(row.table_key) ? 'true' : 'false'"
              >
                <td class="cross-section-table-symbol">
                  <RouterLink :to="instrumentLink(row.symbol, row.dataset_id)">{{ row.symbol }}</RouterLink>
                  <small>{{ lensImplementationLabel }}</small>
                </td>
                <td>
                  <strong :data-tone="toneOf(row)">{{ currentStateLabel(row) }}</strong>
                  <small v-if="!isIndicator">{{ row.stance ? stateLabels[row.stance] ?? row.stance : '方向未提供' }}</small>
                </td>
                <td>
                  <strong>{{ currentStageOrValueLabel(row) }}</strong>
                  <small>{{ tableCurrentSecondary(row) }}</small>
                </td>
                <td class="cross-section-table-previous">
                  <strong>{{ transitionText(row) }}</strong>
                  <small :data-transition="row.transition ? 'changed' : 'unchanged'">{{ transitionCaption(row) }}</small>
                </td>
                <td>
                  <strong class="cross-section-table-change" :data-tone="(row.change ?? 0) >= 0 ? 'positive' : 'negative'">{{ formatChange(row) }}</strong>
                  <small v-if="row.transition">状态变化</small>
                </td>
                <td class="cross-section-table-groups">
                  <span v-for="groupName in row.group_names" :key="groupName" class="cross-section-group-label">{{ groupName }}</span>
                  <small v-if="isRelativeStrength">基准 {{ row.benchmark_key }}</small>
                </td>
                <td class="cross-section-table-quality">
                  <strong :data-tone="row.valid ? 'positive' : 'negative'">{{ tableQualityLabel(row) }}</strong>
                  <small>{{ row.quality_status || '—' }}</small>
                </td>
              </tr>
            </tbody>
          </table>
          <div v-if="!filteredTableRows.length" class="cross-section-table-empty" data-testid="cross-section-table-empty">
            <strong>{{ tableRows.length ? '没有匹配的比较事实' : '当前 Run 没有可展示的横向事实' }}</strong>
            <span>{{ tableRows.length ? '请调整搜索或观察组筛选。' : '当前投影可能为空、部分失败或尚未生成有效数据。' }}</span>
          </div>
        </section>

        <details class="cross-section-quality-details">
          <summary>展开组级质量与来源说明</summary>
          <div class="cross-section-quality-detail-grid">
            <p>RUN <strong>{{ projection.observation_run_id }}</strong></p>
            <p>DATASETS <strong>{{ projection.quality.dataset_ids.map((id) => shortHash(id)).join(' · ') || '—' }}</strong></p>
            <p>SNAPSHOTS <strong>{{ projection.quality.snapshot_ids.length }} immutable group snapshots</strong></p>
            <p>COMPARISON <strong>前一交易日 {{ comparisonDateLabel() }} · {{ comparison.available_group_count }} / {{ comparison.group_count }} groups</strong></p>
          </div>
          <p v-if="comparison.warnings?.length" class="cross-section-warning" role="status">{{ comparison.warnings.join('；') }}</p>
          <p v-if="projection.quality.warnings.length" class="cross-section-warning" role="status">{{ projection.quality.warnings.join('；') }}</p>
        </details>
      </template>
    </template>
  </main>
</template>
