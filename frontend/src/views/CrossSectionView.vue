<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import AppShell from '@/components/AppShell.vue'
import RemoteDecisionPanel from '@/components/decision/RemoteDecisionPanel.vue'
import { api } from '@/api/client'
import type { ObservationRun } from '@/types/api'
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
const runs = ref<ObservationRun[]>([])
const projection = ref<CrossSectionProjection | null>(null)
const selectedRunId = ref('')
const selectedLensId = ref('')
const loading = ref(true)
const loadingProjection = ref(false)
const error = ref('')
const crossSectionAiSource = computed<RemoteDecisionSource>(() => ({
  observation_run_id: selectedRunId.value || undefined,
  lens_id: selectedLensId.value || undefined,
  lens_type: projection.value?.lens.type,
  lens_version: projection.value?.lens.version ?? undefined,
  content_sha256: projection.value?.content_sha256,
}))
const crossSectionAiDisabled = computed(() => !projection.value || !selectedRunId.value || !selectedLensId.value)

const isIndicator = computed(() => props.lensType === 'indicator')
const pageTitle = computed(() => isIndicator.value ? '指标横向扫描' : '策略横向扫描')
const pageKicker = computed(() => isIndicator.value ? 'INDICATOR LENS / OBSERVATION RUN' : 'STRATEGY LENS / OBSERVATION RUN')
const pageDescription = computed(() => isIndicator.value
  ? '从指标角度并列查看所有观察组内的个股状态、分布和变化。'
  : '从策略角度并列查看所有观察组内的个股决策、阶段和变化。')
const activeItem = computed(() => catalog.value.find((item) => item.id === selectedLensId.value) ?? null)
const selectedRun = computed(() => runs.value.find((run) => run.run_id === selectedRunId.value) ?? null)
const readyRuns = computed(() => runs.value.filter((run) => run.status === 'succeeded' || run.status === 'mixed'))
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
const rowsByGroup = computed(() => {
  const result: Record<string, CrossSectionRow[]> = {}
  const groupIds = new Set(visibleGroups.value.map((group) => group.group_id))
  for (const row of projection.value?.rows ?? []) {
    if (!groupIds.has(row.group_id)) continue
    result[row.group_id] ??= []
    result[row.group_id].push(row)
  }
  return result
})
const visibleRows = computed(() => Object.values(rowsByGroup.value).flat())
const visibleValidRowCount = computed(() => visibleRows.value.filter((row) => row.valid).length)
const groupSections = computed(() => [
  {
    key: 'indicator',
    kicker: 'INDICATOR RECOMMENDATION',
    title: '指标推荐',
    description: '自动生成 · 只读',
    groups: visibleGroups.value.filter(isIndicatorGroup),
  },
  {
    key: 'theme',
    kicker: 'SECTOR WATCHLIST',
    title: '主题观察组',
    description: 'Universe 主题组',
    groups: visibleGroups.value.filter((group) => !isIndicatorGroup(group)),
  },
].filter((section) => section.groups.length))

const stateLabels: Record<string, string> = {
  oversold: '超卖',
  overbought: '超买',
  balanced: '平衡',
  positive: '正值',
  negative: '负值',
  zero: '零轴',
  leading: '相对领先',
  lagging: '相对落后',
  inline: '接近基准',
  expanding: '放量',
  contracting: '缩量',
  normal: '正常',
  flat: '持平',
  above: '线上',
  below: '线下',
  missing: '缺失',
  forming: '形成中',
  near_confirmation: '接近确认',
  watching: '观察中',
  armed: '待确认',
  confirmed: '已确认',
  invalidated: '已失效',
  no_setup: '无形态',
  ineligible: '不适用',
  insufficient_data: '数据不足',
  bullish: '偏多',
  bearish: '偏空',
  neutral: '中性',
}

const actionLabels: Record<string, string> = {
  prioritize: '优先关注',
  watch: '观察',
  avoid: '回避',
  wait: '等待',
  no_action: '不行动',
}

type VisualKind = 'rsi-band' | 'ratio-band' | 'zero-centered' | 'binary' | 'decision' | 'plain'

interface VisualScale {
  min: number
  max: number
  zero?: number
}

interface VisualZone {
  key: string
  label: string
  start: number
  end: number
  state: string
}

const activeLensId = computed(() => projection.value?.lens.id ?? selectedLensId.value)
const visualKind = computed<VisualKind>(() => {
  if (props.lensType === 'strategy') return 'decision'
  if (activeLensId.value === 'rsi14') return 'rsi-band'
  if (activeLensId.value === 'volume_ratio_20d') return 'ratio-band'
  if (['macd_histogram', 'relative_strength_20d', 'return_20d'].includes(activeLensId.value)) return 'zero-centered'
  if (activeLensId.value.startsWith('above_ma')) return 'binary'
  return 'plain'
})

const visualScales = computed<Record<string, VisualScale>>(() => {
  const result: Record<string, VisualScale> = {}
  for (const group of projection.value?.groups ?? []) {
    const distribution = group.distribution
    if (visualKind.value === 'rsi-band') {
      result[group.group_id] = { min: 0, max: 100 }
      continue
    }
    if (visualKind.value === 'ratio-band') {
      const min = typeof distribution.min === 'number' ? Math.min(0.5, distribution.min) : 0.5
      const max = typeof distribution.max === 'number' ? Math.max(1.5, distribution.max) : 1.5
      result[group.group_id] = { min, max }
      continue
    }
    if (visualKind.value === 'zero-centered') {
      const min = typeof distribution.min === 'number' ? Math.abs(distribution.min) : 0
      const max = typeof distribution.max === 'number' ? Math.abs(distribution.max) : 0
      const bound = Math.max(min, max, 1)
      result[group.group_id] = { min: -bound, max: bound, zero: 0 }
      continue
    }
    if (visualKind.value === 'decision') {
      result[group.group_id] = { min: -100, max: 100, zero: 0 }
      continue
    }
    if (visualKind.value === 'binary') {
      result[group.group_id] = { min: 0, max: 1 }
      continue
    }
    result[group.group_id] = { min: 0, max: 1 }
  }
  return result
})

function routeLensId() {
  const key = isIndicator.value ? 'indicatorId' : 'strategyId'
  const value = route.params[key]
  return typeof value === 'string' ? value : ''
}

function routeName() {
  return isIndicator.value ? 'indicator-cross-section' : 'strategy-cross-section'
}

function indexRouteName() {
  return isIndicator.value ? 'indicators' : 'strategies'
}

function shortHash(value: string | null | undefined) {
  return value ? `${value.slice(0, 10)}…` : '—'
}

function formatNumber(value: number | null | undefined, digits = 2) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—'
}

function formatChange(row: CrossSectionRow) {
  if (row.change === null || row.change === undefined) return '—'
  const digits = row.unit === 'ratio' ? 2 : row.unit === 'boolean' ? 0 : 2
  return `${row.change >= 0 ? '+' : ''}${row.change.toFixed(digits)}${row.unit === 'percent' ? '%' : ''}`
}

function formatSigned(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}`
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
  if (props.lensType === 'strategy') return row.previous_value.toFixed(0)
  return row.previous_display_value ?? formatNumber(row.previous_value)
}

function previousStageOf(row: CrossSectionRow) {
  const stage = row.previous_state ?? 'missing'
  return stateLabels[stage] ?? stage
}

function previousSummaryOf(row: CrossSectionRow) {
  if (row.previous_value === null || row.previous_value === undefined) return '前一交易日无可比数据'
  if (props.lensType === 'strategy') {
    const action = actionLabels[row.previous_action ?? ''] ?? row.previous_action ?? '—'
    return `前日 ${previousValueOf(row)} · ${action} · ${previousStageOf(row)}`
  }
  return `前日 ${previousValueOf(row)} · ${stateLabels[row.previous_state ?? ''] ?? row.previous_state ?? '—'}`
}

function previousStrategySummaryOf(row: CrossSectionRow) {
  if (row.previous_value === null || row.previous_value === undefined) return '前日无可比决策'
  const action = actionLabels[row.previous_action ?? ''] ?? row.previous_action ?? '—'
  return `前日 ${action} · ${previousStageOf(row)}`
}

function indicatorThresholds(row?: CrossSectionRow) {
  const itemThresholds = (isIndicator.value ? (activeItem.value ?? projection.value?.indicator)?.thresholds : undefined) ?? {}
  return row?.thresholds ?? itemThresholds
}

function scaleForGroup(groupId: string): VisualScale {
  return visualScales.value[groupId] ?? { min: 0, max: 1 }
}

function clamp(value: number, min = 0, max = 100) {
  return Math.min(max, Math.max(min, value))
}

function scalePosition(value: number | null | undefined, scale: VisualScale) {
  if (value === null || value === undefined || !Number.isFinite(value) || scale.max === scale.min) return null
  return clamp(((value - scale.min) / (scale.max - scale.min)) * 100)
}

function markerStyle(value: number | null | undefined, groupId: string) {
  const position = scalePosition(value, scaleForGroup(groupId))
  return position === null ? {} : { left: `${position}%` }
}

function rsiZoneStyle(row: CrossSectionRow | undefined, zone: 'oversold' | 'balanced' | 'overbought') {
  const thresholds = indicatorThresholds(row)
  const oversold = clamp(Number(thresholds.oversold ?? 30), 0, 100)
  const overbought = clamp(Number(thresholds.overbought ?? 70), oversold, 100)
  const bounds = {
    oversold: [0, oversold],
    balanced: [oversold, overbought],
    overbought: [overbought, 100],
  }[zone]
  return { left: `${bounds[0]}%`, width: `${bounds[1] - bounds[0]}%` }
}

function ratioZoneStyle(row: CrossSectionRow | undefined, groupId: string, zone: 'contracting' | 'normal' | 'expanding') {
  const scale = scaleForGroup(groupId)
  const thresholds = indicatorThresholds(row)
  const contracting = Math.max(scale.min, Number(thresholds.contraction ?? thresholds.contracting ?? 0.8))
  const expanding = Math.min(scale.max, Number(thresholds.expansion ?? thresholds.expanding ?? 1.2))
  const bounds = {
    contracting: [scale.min, contracting],
    normal: [contracting, expanding],
    expanding: [expanding, scale.max],
  }[zone]
  const start = scalePosition(bounds[0], scale) ?? 0
  const end = scalePosition(bounds[1], scale) ?? 100
  return { left: `${start}%`, width: `${Math.max(0, end - start)}%` }
}

function zeroPosition(groupId: string) {
  const scale = scaleForGroup(groupId)
  return scalePosition(scale.zero ?? 0, scale) ?? 50
}

function zeroBarStyle(row: CrossSectionRow, groupId: string) {
  const position = scalePosition(row.value, scaleForGroup(groupId))
  const zero = zeroPosition(groupId)
  if (position === null) return {}
  return {
    left: `${Math.min(position, zero)}%`,
    width: `${Math.abs(position - zero)}%`,
  }
}

function scoreMarkerStyle(row: CrossSectionRow) {
  return scoreMarkerStyleForValue(row.score)
}

function scorePreviousMarkerStyle(row: CrossSectionRow) {
  return scoreMarkerStyleForValue(row.previous_value)
}

function scoreMarkerStyleForValue(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) return {}
  return { left: `${clamp(((value + 100) / 200) * 100)}%` }
}

function visualZones(row: CrossSectionRow | undefined, groupId: string): VisualZone[] {
  if (visualKind.value === 'rsi-band') {
    return [
      { key: 'oversold', label: '超卖', ...rsiBounds(row, 'oversold'), state: 'oversold' },
      { key: 'balanced', label: '平衡', ...rsiBounds(row, 'balanced'), state: 'balanced' },
      { key: 'overbought', label: '超买', ...rsiBounds(row, 'overbought'), state: 'overbought' },
    ]
  }
  if (visualKind.value === 'ratio-band') {
    return (['contracting', 'normal', 'expanding'] as const).map((zone) => {
      const style = ratioZoneStyle(row, groupId, zone)
      return { key: zone, label: stateLabels[zone], start: Number.parseFloat(style.left), end: Number.parseFloat(style.left) + Number.parseFloat(style.width), state: zone }
    })
  }
  return []
}

function rsiBounds(row: CrossSectionRow | undefined, zone: 'oversold' | 'balanced' | 'overbought') {
  const thresholds = indicatorThresholds(row)
  const oversold = clamp(Number(thresholds.oversold ?? 30), 0, 100)
  const overbought = clamp(Number(thresholds.overbought ?? 70), oversold, 100)
  const bounds = {
    oversold: [0, oversold],
    balanced: [oversold, overbought],
    overbought: [overbought, 100],
  }[zone]
  return { start: bounds[0], end: bounds[1] }
}

function distanceLabel(row: CrossSectionRow) {
  if (!isIndicator.value || row.value === null || row.value === undefined) return ''
  if (activeLensId.value === 'rsi14') {
    const thresholds = indicatorThresholds(row)
    const oversold = Number(thresholds.oversold ?? 30)
    const overbought = Number(thresholds.overbought ?? 70)
    if (row.state === 'oversold') return `距${formatNumber(oversold)} ${formatNumber(oversold - row.value)}`
    if (row.state === 'overbought') return `距${formatNumber(overbought)} ${formatNumber(row.value - overbought)}`
    return `距边界 ${formatNumber(Math.min(row.value - oversold, overbought - row.value))}`
  }
  if (row.threshold_distance === null || row.threshold_distance === undefined) return ''
  return `距参考 ${formatNumber(row.threshold_distance)}`
}

function cardAriaLabel(row: CrossSectionRow) {
  const lensName = activeItem.value?.name ?? projection.value?.indicator?.name ?? projection.value?.strategy?.name ?? '横向扫描'
  const transition = row.transition
    ? `，状态由${stateLabels[row.transition.from] ?? row.transition.from}切换至${stateLabels[row.transition.to] ?? row.transition.to}`
    : ''
  if (props.lensType === 'strategy') {
    return `${row.symbol}，${lensName}，${stateOf(row)}，动作${actionOf(row)}，阶段${stageOf(row)}，score ${scoreOf(row)}${row.change === null || row.change === undefined ? '' : `，变化${formatChange(row)}`}${transition}`
  }
  return `${row.symbol}，${lensName}，${statusOf(row)}，当前值${valueOf(row)}${row.change === null || row.change === undefined ? '' : `，变化${formatChange(row)}`}${transition}`
}

function visualAriaLabel(row: CrossSectionRow, groupId: string) {
  if (visualKind.value === 'rsi-band') {
    return `${row.symbol} RSI 14 区间轨道，当前 ${row.display_value}，${statusOf(row)}，范围 0 到 100，阈值 30 和 70`
  }
  if (visualKind.value === 'zero-centered') {
    return `${row.symbol} 零轴轨道，当前 ${row.display_value}，零轴位置 ${formatNumber(zeroPosition(groupId), 0)}%`
  }
  if (visualKind.value === 'ratio-band') return `${row.symbol} 成交量比率轨道，当前 ${row.display_value}`
  if (visualKind.value === 'binary') return `${row.symbol} 均线位置，当前 ${statusOf(row)}`
  if (visualKind.value === 'decision') return `${row.symbol} 策略 score 零轴轨道，当前 ${scoreOf(row)}，${stateOf(row)}，${actionOf(row)}，阶段 ${stageOf(row)}，范围 -100 到 100`
  return `${row.symbol} 当前值 ${valueOf(row)}`
}

function valueOf(row: CrossSectionRow) {
  if (props.lensType === 'strategy') {
    return row.score === null || row.score === undefined ? '—' : row.score.toFixed(0)
  }
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
  if (props.lensType === 'strategy') {
    return stateLabels[row.stance ?? ''] ?? row.stance ?? '—'
  }
  return stateLabels[row.state] ?? row.state
}

function toneOf(row: CrossSectionRow) {
  if (props.lensType === 'strategy') {
    if (row.stance === 'bullish') return 'positive'
    if (row.stance === 'bearish') return 'negative'
    if (row.stance === 'insufficient_data' || !row.valid) return 'attention'
    return 'neutral'
  }
  const state = row.state
  if (['positive', 'leading', 'above', 'expanding', 'confirmed'].includes(state)) return 'positive'
  if (['negative', 'lagging', 'below', 'contracting', 'overbought', 'invalidated'].includes(state)) return 'negative'
  if (['oversold', 'armed', 'watching'].includes(state)) return 'attention'
  return 'neutral'
}

function groupStateTone(state: string) {
  if (props.lensType === 'strategy') {
    if (state === 'bullish') return 'positive'
    if (state === 'bearish') return 'negative'
    if (state === 'insufficient_data') return 'attention'
    return 'neutral'
  }
  if (['positive', 'leading', 'above', 'expanding', 'confirmed'].includes(state)) return 'positive'
  if (['negative', 'lagging', 'below', 'contracting', 'invalidated'].includes(state)) return 'negative'
  if (['oversold', 'armed', 'watching'].includes(state)) return 'attention'
  return 'neutral'
}

function strategyStageTone(stage: string) {
  if (stage === 'confirmed') return 'positive'
  if (stage === 'invalidated') return 'negative'
  if (['armed', 'watching', 'near_confirmation'].includes(stage)) return 'attention'
  if (stage === 'insufficient_data') return 'attention'
  return 'neutral'
}

function groupDistribution(group: CrossSectionProjection['groups'][number]) {
  if (props.lensType === 'strategy' && group.stance_counts && Object.keys(group.stance_counts).length) {
    return group.stance_counts
  }
  return group.state_counts
}

function groupDistributionEntries(group: CrossSectionProjection['groups'][number]) {
  return Object.entries(groupDistribution(group))
}

function groupStageDistributionEntries(group: CrossSectionProjection['groups'][number]) {
  return Object.entries(group.state_counts)
}

function firstRowForGroup(groupId: string) {
  return rowsByGroup.value[groupId]?.[0]
}

function groupNameOf(group: CrossSectionProjection['groups'][number]) {
  const rawName = rawGroupNameOf(group)
  if (group.group_id === 'universe-core-watchlist' || rawName === '核心关注列表') return '指标推荐'
  return rawName
}

function rawGroupNameOf(group: CrossSectionProjection['groups'][number]) {
  return group.group_name || group.display_name || group.group_id
}

function isIndicatorGroup(group: CrossSectionProjection['groups'][number]) {
  const rawName = rawGroupNameOf(group)
  return group.group_id === 'universe-core-watchlist' || rawName === '指标推荐' || rawName === '核心关注列表'
}

function isLegacySelfSelectedGroup(group: CrossSectionProjection['groups'][number]) {
  const rawName = rawGroupNameOf(group)
  return ['核心观察组', '自选组', '自选个股'].includes(rawName)
}

function groupScaleMarkerStyle(group: CrossSectionProjection['groups'][number], value: number | null) {
  const position = scalePosition(value, scaleForGroup(group.group_id))
  return position === null ? {} : { left: `${position}%` }
}

function groupScaleLabel(group: CrossSectionProjection['groups'][number]) {
  const distribution = group.distribution
  if (visualKind.value === 'decision') return `策略 score -100 至 +100；-25 以下偏空，-25 至 25 中性，25 以上偏多，0 为零轴；${groupNameOf(group)} 中位数 ${formatNumber(distribution.median)}`
  if (visualKind.value === 'rsi-band') return 'RSI 0–100，30 以下超卖，30–70 平衡，70 以上超买'
  if (visualKind.value === 'ratio-band') return '成交量比率区间，0.8 以下缩量，0.8–1.2 正常，1.2 以上放量'
  if (visualKind.value === 'zero-centered') return `零轴比较，组内范围 ${formatNumber(distribution.min)} 至 ${formatNumber(distribution.max)}`
  return `${group.group_name} ${formatNumber(distribution.min)} 至 ${formatNumber(distribution.max)}`
}

function instrumentLink(symbol: string, datasetId: string) {
  return {
    name: 'instrument-decision',
    params: { symbol },
    query: {
      dataset: datasetId,
      ...(projection.value?.observation_run_id ? { run: projection.value.observation_run_id } : {}),
    },
  }
}

function statusOf(row: CrossSectionRow) {
  if (row.status === 'missing' || !row.valid) return '数据缺失'
  if (props.lensType === 'strategy') return stateOf(row)
  return stateOf(row)
}

async function loadProjection() {
  projection.value = null
  if (!selectedRunId.value || !selectedLensId.value) return
  loadingProjection.value = true
  error.value = ''
  try {
    projection.value = isIndicator.value
      ? await api.getIndicatorCrossSection(selectedRunId.value, selectedLensId.value)
      : await api.getStrategyCrossSection(selectedRunId.value, selectedLensId.value)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '横向投影加载失败。'
  } finally {
    loadingProjection.value = false
  }
}

async function selectLens(id: string) {
  selectedLensId.value = id
  await router.push({
    name: routeName(),
    params: { [isIndicator.value ? 'indicatorId' : 'strategyId']: id },
    query: selectedRunId.value ? { run: selectedRunId.value } : undefined,
  })
  await loadProjection()
}

async function selectRun() {
  if (!readyRuns.value.some((run) => run.run_id === selectedRunId.value)) {
    selectedRunId.value = readyRuns.value[0]?.run_id || ''
  }
  await router.replace({
    name: route.params[isIndicator.value ? 'indicatorId' : 'strategyId'] ? routeName() : indexRouteName(),
    params: route.params,
    query: selectedRunId.value ? { run: selectedRunId.value } : undefined,
  })
  await loadProjection()
}

async function loadPage() {
  loading.value = true
  error.value = ''
  projection.value = null
  try {
    const [items, runItems] = await Promise.all([
      isIndicator.value ? api.listIndicatorCatalog() : api.listStrategyCatalog(),
      api.listObservationRuns(30),
    ])
    catalog.value = items
    runs.value = runItems
    selectedLensId.value = routeLensId() || items[0]?.id || ''
    const queryRun = typeof route.query.run === 'string' ? route.query.run : ''
    selectedRunId.value = queryRun && runItems.some((run) => run.run_id === queryRun && (run.status === 'succeeded' || run.status === 'mixed'))
      ? queryRun
      : runItems.find((run) => run.status === 'succeeded' || run.status === 'mixed')?.run_id || ''
    await loadProjection()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '扫描目录加载失败。'
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.lensType, route.params.indicatorId, route.params.strategyId, route.query.run],
  () => {
    const lensId = routeLensId()
    const lensChanged = Boolean(lensId && lensId !== selectedLensId.value)
    if (lensId) selectedLensId.value = lensId
    if (typeof route.query.run === 'string' && route.query.run !== selectedRunId.value && readyRuns.value.some((run) => run.run_id === route.query.run)) {
      selectedRunId.value = route.query.run
      void loadProjection()
    } else if (lensChanged) {
      void loadProjection()
    }
  },
)

onMounted(() => { void loadPage() })
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
              {{ run.trading_date }} · {{ run.status }}
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
            <strong>{{ item.name }}</strong>
            <small>{{ item.version ?? item.feature_version ?? '—' }}</small>
          </button>
        </div>
      </section>

      <section v-if="loadingProjection" class="empty-panel cross-section-empty"><p>正在读取冻结投影…</p></section>
      <template v-else-if="projection">
        <section class="cross-section-stat-strip">
          <div><span>AS OF</span><strong>{{ projection.trading_date }}</strong><small>收市冻结快照</small></div>
          <div><span>COMPARE</span><strong>{{ comparisonStatusLabel() }}</strong><small>前一交易日 {{ comparisonDateLabel() }} · {{ comparison.available_group_count }} / {{ comparison.group_count }} 组</small></div>
          <div><span>GROUPS</span><strong>{{ visibleGroups.length }} / {{ projection.quality.projected_group_count }}</strong><small>active groups shown</small></div>
          <div><span>SYMBOLS</span><strong>{{ visibleValidRowCount }} / {{ visibleRows.length }}</strong><small>{{ projection.quality.status }} · active groups</small></div>
          <div><span>CHANGES</span><strong>{{ projection.transitions.length }}</strong><small>state transitions</small></div>
        </section>

        <section class="cross-section-focus-bar">
          <div>
            <p class="section-kicker">{{ isIndicator ? 'INDICATOR FOCUS' : 'STRATEGY FOCUS' }}</p>
            <h2>{{ activeItem?.name ?? (isIndicator ? projection.indicator?.name : projection.strategy?.name) }}</h2>
            <p>{{ activeItem?.description ?? '' }}</p>
            <div v-if="isIndicator" class="cross-section-focus-legend" aria-label="指标状态说明">
              <span v-if="activeLensId === 'rsi14'" data-state="oversold"><i></i>超卖 &lt; 30</span>
              <span v-if="activeLensId === 'rsi14'" data-state="balanced"><i></i>平衡 30–70</span>
              <span v-if="activeLensId === 'rsi14'" data-state="overbought"><i></i>超买 &gt; 70</span>
              <span data-state="comparison"><i></i>实心标记当前 · 暗标记前一交易日</span>
              <span data-state="neutral"><i></i>颜色表示状态，不是交易指令</span>
            </div>
            <div v-else class="cross-section-focus-legend cross-section-focus-legend--strategy" aria-label="策略状态说明">
              <span data-state="bullish"><i></i>偏多</span>
              <span data-state="neutral"><i></i>中性</span>
              <span data-state="bearish"><i></i>偏空</span>
              <span data-state="stage"><i></i>阶段与 score 分开显示</span>
              <span data-state="comparison"><i></i>实心标记当前 · 暗标记前一交易日</span>
            </div>
          </div>
          <div class="cross-section-provenance">
            <span>VERSION <b>{{ projection.lens.version ?? '—' }}</b></span>
            <span>RUN <b>{{ shortHash(projection.observation_run_id) }}</b></span>
            <span>CONTENT <b>{{ shortHash(projection.content_sha256) }}</b></span>
          </div>
        </section>

        <section class="cross-section-groups">
          <div class="cross-section-section-heading">
            <div><p class="section-kicker">{{ isIndicator ? 'CROSS-SECTION HEATMAP' : 'STRATEGY DECISION MAP' }}</p><h2>{{ isIndicator ? '按观察组查看全部个股' : '按观察组查看策略决策' }}</h2></div>
            <small>{{ isIndicator ? '先看分布，再看状态卡片；卡片颜色只表示确定性状态' : '先看 stance，再看 setup stage 与 score；不代表概率' }}</small>
          </div>

          <section v-for="section in groupSections" :key="section.key" class="cross-section-group-section">
            <div class="cross-section-group-section-heading">
              <div>
                <p class="section-kicker">{{ section.kicker }}</p>
                <h3>{{ section.title }}</h3>
              </div>
              <small>{{ section.description }}</small>
            </div>
            <article v-for="group in section.groups" :key="group.group_id" class="cross-section-group-card">
            <header class="cross-section-group-head">
              <div>
                <p class="section-kicker">{{ group.group_id }} · V{{ group.group_version }}</p>
                <h3>{{ groupNameOf(group) }}</h3>
              </div>
              <div class="cross-section-group-meta">
                <span>{{ group.valid_symbol_count }} / {{ group.symbol_count }} valid</span>
                <RouterLink :to="`/groups/${group.group_id}`">打开观察组 →</RouterLink>
              </div>
            </header>
            <div class="cross-section-group-summary">
              <div class="cross-section-group-comparison" aria-label="当前与前一交易日组内中位数对比">
                <span><b>当前 {{ group.trading_date }}</b> · {{ isIndicator ? 'MEDIAN' : 'SCORE MEDIAN' }} {{ formatNumber(group.distribution.median) }}</span>
                <span><b>前日 {{ group.previous_trading_date ?? '—' }}</b> · {{ isIndicator ? 'MEDIAN' : 'SCORE MEDIAN' }} {{ formatNumber(group.previous_distribution?.median) }}</span>
                <span :data-tone="(group.distribution_median_change ?? 0) >= 0 ? 'positive' : 'negative'">Δ {{ formatSigned(group.distribution_median_change) }}</span>
              </div>
              <div class="cross-section-group-distribution" aria-label="观察组状态分布">
                <span
                  v-for="([state, count]) in groupDistributionEntries(group)"
                  :key="state"
                  :data-state="state"
                  :data-tone="groupStateTone(state)"
                  :style="{ flexGrow: count }"
                  :title="`${stateLabels[state] ?? state} ${count}`"
                ></span>
              </div>
              <div class="cross-section-group-state-counts" aria-label="观察组状态计数">
                <template v-if="isIndicator">
                  <span v-for="([state, count]) in groupDistributionEntries(group)" :key="`count-${state}`" :data-state="state">
                    <i></i>{{ stateLabels[state] ?? state }} {{ count }}
                  </span>
                </template>
                <template v-else>
                  <span v-for="([stance, count]) in groupDistributionEntries(group)" :key="`stance-${stance}`" :data-state="stance">
                    <i></i>{{ stateLabels[stance] ?? stance }} {{ count }}
                  </span>
                </template>
              </div>
              <div v-if="!isIndicator" class="cross-section-group-stage-counts" aria-label="观察组 setup 阶段分布">
                <small>SETUP STAGE</small>
                <span v-for="([stage, count]) in groupStageDistributionEntries(group)" :key="`stage-${stage}`" :data-tone="strategyStageTone(stage)">
                  {{ stateLabels[stage] ?? stage }} {{ count }}
                </span>
              </div>
            </div>
            <div
              class="cross-section-group-scale"
              :data-visual-kind="visualKind"
              role="img"
              :aria-label="groupScaleLabel(group)"
            >
              <template v-if="visualKind === 'rsi-band'">
                <span class="cross-section-scale-zone" data-state="oversold" :style="rsiZoneStyle(firstRowForGroup(group.group_id), 'oversold')"></span>
                <span class="cross-section-scale-zone" data-state="balanced" :style="rsiZoneStyle(firstRowForGroup(group.group_id), 'balanced')"></span>
                <span class="cross-section-scale-zone" data-state="overbought" :style="rsiZoneStyle(firstRowForGroup(group.group_id), 'overbought')"></span>
              </template>
              <template v-else-if="visualKind === 'ratio-band'">
                <span v-for="zone in visualZones(firstRowForGroup(group.group_id), group.group_id)" :key="zone.key" class="cross-section-scale-zone" :data-state="zone.state" :style="{ left: `${zone.start}%`, width: `${zone.end - zone.start}%` }"></span>
              </template>
              <template v-else-if="visualKind === 'decision'">
                <span class="cross-section-scale-zone" data-state="bearish" :style="{ left: '0%', width: '37.5%' }"></span>
                <span class="cross-section-scale-zone" data-state="neutral" :style="{ left: '37.5%', width: '25%' }"></span>
                <span class="cross-section-scale-zone" data-state="bullish" :style="{ left: '62.5%', width: '37.5%' }"></span>
                <span class="cross-section-zero-line" style="left: 50%"></span>
              </template>
              <span v-else class="cross-section-scale-zone" data-state="neutral" :style="{ left: '0%', width: '100%' }"></span>
              <i class="cross-section-scale-marker cross-section-scale-marker--q1" :style="groupScaleMarkerStyle(group, group.distribution.q1)" title="Q1"></i>
              <i class="cross-section-scale-marker cross-section-scale-marker--median" :style="groupScaleMarkerStyle(group, group.distribution.median)" title="MEDIAN"></i>
              <i class="cross-section-scale-marker cross-section-scale-marker--q3" :style="groupScaleMarkerStyle(group, group.distribution.q3)" title="Q3"></i>
              <i v-if="group.previous_distribution?.median !== null && group.previous_distribution?.median !== undefined" class="cross-section-scale-marker cross-section-scale-marker--previous" :style="groupScaleMarkerStyle(group, group.previous_distribution.median)" title="前一交易日中位数"></i>
              <span class="cross-section-scale-label cross-section-scale-label--start">{{ visualKind === 'rsi-band' ? '0' : visualKind === 'decision' ? '-100' : formatNumber(group.distribution.min) }}</span>
              <span class="cross-section-scale-label cross-section-scale-label--middle">MEDIAN {{ formatNumber(group.distribution.median) }}</span>
              <span class="cross-section-scale-label cross-section-scale-label--end">{{ visualKind === 'rsi-band' ? '100' : visualKind === 'decision' ? '+100' : formatNumber(group.distribution.max) }}</span>
            </div>
            <div class="cross-section-symbol-grid" :data-visual-kind="visualKind">
              <p
                v-if="!(rowsByGroup[group.group_id] ?? []).length"
                class="cross-section-no-symbols"
              >本组没有可展示的标的。</p>
              <RouterLink
                v-for="row in rowsByGroup[group.group_id] ?? []"
                :key="row.id"
                :id="`card-${row.id}`"
                class="cross-section-symbol-card"
                :class="{ 'is-transition': Boolean(row.transition) }"
                :data-state="isIndicator ? row.state : row.stance"
                :data-stage="!isIndicator ? row.setup_progress?.stage ?? row.state : undefined"
                :data-tone="toneOf(row)"
                :to="instrumentLink(row.symbol, row.dataset_id)"
                :aria-label="cardAriaLabel(row)"
              >
                <span class="cross-section-symbol-card-head">
                  <strong>{{ row.symbol }}</strong>
                  <span class="cross-section-state-badge" :data-state="isIndicator ? row.state : row.stance">{{ statusOf(row) }}</span>
                </span>
                <span v-if="!isIndicator" class="cross-section-strategy-summary">
                  <b>{{ actionOf(row) }}</b>
                  <span :data-tone="strategyStageTone(String(row.setup_progress?.stage ?? row.state))">{{ stageOf(row) }}</span>
                </span>
                <span v-if="!isIndicator" class="cross-section-strategy-previous">{{ previousStrategySummaryOf(row) }}</span>
                <span v-if="row.transition" class="cross-section-symbol-card-transition">↗ 状态切换</span>
                <span class="cross-section-symbol-visual" :data-visual-kind="visualKind" role="img" :aria-label="visualAriaLabel(row, group.group_id)">
                  <template v-if="visualKind === 'rsi-band'">
                    <span v-for="zone in visualZones(row, group.group_id)" :key="zone.key" class="cross-section-visual-zone" :data-state="zone.state" :style="{ left: `${zone.start}%`, width: `${zone.end - zone.start}%` }"></span>
                    <i v-if="row.previous_value !== null && row.previous_value !== undefined" class="cross-section-visual-marker cross-section-visual-marker--previous" :style="markerStyle(row.previous_value, group.group_id)"></i>
                    <i v-if="row.value !== null && row.value !== undefined" class="cross-section-visual-marker cross-section-visual-marker--current" :style="markerStyle(row.value, group.group_id)"></i>
                  </template>
                  <template v-else-if="visualKind === 'ratio-band'">
                    <span v-for="zone in visualZones(row, group.group_id)" :key="zone.key" class="cross-section-visual-zone" :data-state="zone.state" :style="{ left: `${zone.start}%`, width: `${zone.end - zone.start}%` }"></span>
                    <i v-if="row.previous_value !== null && row.previous_value !== undefined" class="cross-section-visual-marker cross-section-visual-marker--previous" :style="markerStyle(row.previous_value, group.group_id)"></i>
                    <i v-if="row.value !== null && row.value !== undefined" class="cross-section-visual-marker cross-section-visual-marker--current" :style="markerStyle(row.value, group.group_id)"></i>
                  </template>
                  <template v-else-if="visualKind === 'zero-centered'">
                    <span class="cross-section-zero-line" :style="{ left: `${zeroPosition(group.group_id)}%` }"></span>
                    <span class="cross-section-zero-bar" :data-tone="toneOf(row)" :style="zeroBarStyle(row, group.group_id)"></span>
                    <i v-if="row.previous_value !== null && row.previous_value !== undefined" class="cross-section-visual-marker cross-section-visual-marker--previous" :style="markerStyle(row.previous_value, group.group_id)"></i>
                    <i v-if="row.value !== null && row.value !== undefined" class="cross-section-visual-marker cross-section-visual-marker--current" :style="markerStyle(row.value, group.group_id)"></i>
                  </template>
                  <template v-else-if="visualKind === 'binary'">
                    <span class="cross-section-binary-track"><i :data-state="row.state" :style="{ width: row.value === 1 ? '100%' : '0%' }"></i></span>
                    <i v-if="row.previous_value !== null && row.previous_value !== undefined" class="cross-section-visual-marker cross-section-visual-marker--previous" :style="markerStyle(row.previous_value, group.group_id)"></i>
                  </template>
                  <template v-else-if="visualKind === 'decision'">
                    <span class="cross-section-decision-track">
                      <span class="cross-section-decision-zone" data-state="bearish"></span>
                      <span class="cross-section-decision-zone" data-state="neutral"></span>
                      <span class="cross-section-decision-zone" data-state="bullish"></span>
                      <span class="cross-section-decision-zero"></span>
                      <i v-if="row.previous_value !== null && row.previous_value !== undefined" class="cross-section-decision-marker cross-section-decision-marker--previous" :style="scorePreviousMarkerStyle(row)"></i>
                      <i v-if="row.score !== null && row.score !== undefined" class="cross-section-decision-marker cross-section-decision-marker--current" :data-tone="toneOf(row)" :style="scoreMarkerStyle(row)"></i>
                    </span>
                  </template>
                  <span v-else class="cross-section-plain-track"></span>
                </span>
                <span class="cross-section-symbol-card-value">
                  <b>{{ isIndicator ? valueOf(row) : scoreOf(row) }}</b>
                  <small v-if="isIndicator">{{ distanceLabel(row) }}</small>
                  <small v-else>score · -100 至 +100</small>
                </span>
                <span class="cross-section-symbol-card-previous">{{ previousSummaryOf(row) }}</span>
                <span class="cross-section-symbol-card-meta">
                  <span class="cross-section-change" :data-tone="(row.change ?? 0) >= 0 ? 'positive' : 'negative'">{{ formatChange(row) }}</span>
                  <span class="cross-section-quality" :data-tone="row.valid ? 'positive' : 'negative'">{{ row.valid ? 'OK' : 'MISSING' }}</span>
                </span>
              </RouterLink>
            </div>
            </article>
          </section>
        </section>

        <section class="cross-section-bottom-grid">
          <article class="cross-section-card cross-section-transitions">
            <header class="cross-section-card-head"><div><p class="section-kicker">STATE TRANSITIONS</p><h3>前一交易日以来发生变化的个股</h3></div><span>{{ projection.transitions.length }} changes</span></header>
            <div v-if="!projection.transitions.length" class="cross-section-no-transition">本次 Run 没有可由前一快照确认的状态切换。</div>
            <RouterLink v-for="transition in projection.transitions.slice(0, 12)" :key="transition.id" class="cross-section-transition-row" :to="instrumentLink(transition.symbol, transition.dataset_id)">
              <span><strong>{{ transition.symbol }}</strong><small>{{ transition.group_name }}</small></span>
              <span class="cross-section-transition-state"><b>{{ stateLabels[transition.transition?.from ?? ''] ?? transition.transition?.from }}</b><i>→</i><b>{{ stateLabels[transition.transition?.to ?? ''] ?? transition.transition?.to }}</b></span>
              <span>{{ transition.change === null ? '—' : `${transition.change >= 0 ? '+' : ''}${transition.change.toFixed(2)}` }}</span>
            </RouterLink>
          </article>

          <article class="cross-section-card cross-section-quality">
            <header class="cross-section-card-head"><div><p class="section-kicker">PROVENANCE / QUALITY</p><h3>这次横向扫描的来源</h3></div><span :data-tone="projection.quality.status === 'ok' ? 'positive' : 'attention'">{{ projection.quality.status }}</span></header>
            <dl>
              <div><dt>OBSERVATION RUN</dt><dd>{{ projection.observation_run_id }}</dd></div>
              <div><dt>DATASETS</dt><dd>{{ projection.quality.dataset_ids.map((id) => shortHash(id)).join(' · ') }}</dd></div>
              <div><dt>SNAPSHOTS</dt><dd>{{ projection.quality.snapshot_ids.length }} immutable group snapshots</dd></div>
              <div><dt>COMPARISON</dt><dd>前一交易日 {{ comparisonDateLabel() }} · {{ comparison.available_group_count }} / {{ comparison.group_count }} groups</dd></div>
              <div><dt>AI</dt><dd>用户确认后调用 Anomalo Workflow</dd></div>
            </dl>
            <p v-if="projection.quality.warnings.length" class="cross-section-warning">{{ projection.quality.warnings.join('；') }}</p>
          </article>
        </section>
      </template>
    </template>
  </main>
</template>
