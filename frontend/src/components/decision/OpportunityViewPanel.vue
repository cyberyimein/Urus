<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { api } from '@/api/client'
import type {
  DailyOpportunityViewResponse,
  OpportunityView,
  OpportunityViewReferenceKind,
  OpportunityViewRow,
  OpportunityViewStatus,
} from '@/types/opportunityView'

const props = withDefaults(defineProps<{
  datasetId?: string | null
  symbol?: string | null
  disabled?: boolean
}>(), {
  datasetId: null,
  symbol: null,
  disabled: false,
})

type PanelState = 'loading' | 'success' | 'unknown' | 'error'

const state = ref<PanelState>('unknown')
const row = ref<OpportunityViewRow | null>(null)
const response = ref<DailyOpportunityViewResponse | null>(null)
const error = ref('')
const requestToken = ref(0)
const activeRequestController = ref<AbortController | null>(null)

const statusLabels: Record<string, string> = {
  unavailable: '当前无可用计划',
  stale: '证据已过时',
  invalidated: '历史计划已失效',
  expired: '历史计划已到期',
  preparing: '等待突破确认',
  triggered_unverified: '突破待验证',
  in_entry_zone: '位于研究区间',
  extended: '已超出研究区间',
  waiting: '等待重新验证',
}

const reasonLabels: Record<string, string> = {
  coverage_miss: '证据覆盖不足',
  insufficient_history: '历史日线不足',
  asset_type_ineligible: '资产类型不适用',
  not_enabled: '标的未启用',
  price_below_minimum: '价格低于最低门槛',
  liquidity_below_minimum: '流动性低于门槛',
  invalid_price_basis: '价格口径无效',
  invalid_structure: '结构条件未成立',
  scan_not_eligible: '扫描未通过资格条件',
  daily_reference_is_not_current_quote: '日线参考价不是当前报价',
  trigger_path_not_observed: '触发路径未完整观察',
  trigger_first_observation_extended: '首次观察时已超过上限',
  current_quote_in_research_zone: '当前参考值位于研究区间',
  daily_close_cannot_prove_current_entry_zone: '日线收盘价不能证明当前可参与',
  evidence_quality_not_complete: '证据质量尚未完整',
  event_risk_unknown: '事件风险未知',
  near_term_event_restricts_participation: '临近事件限制参与条件',
  event_within_holding_window: '持有观察期内存在事件',
  no_visible_daily_bars: '没有可见日线',
  daily_close_older_than_current_session: '日线收盘早于当前交易时段',
  calendar_required_to_prove_expiry: '需要交易日历判断是否到期',
}

const qualityLabels: Record<string, string> = {
  ok: '完整',
  partial: '部分完整',
  unavailable: '不可用',
  unknown: '未知',
}

const freshnessLabels: Record<string, string> = {
  fresh: '新鲜',
  stale: '过时',
  unknown: '未知',
}

const normalizedDatasetId = computed(() => props.datasetId?.trim() ?? '')
const normalizedSymbol = computed(() => normalizeSymbol(props.symbol))
const opportunity = computed<OpportunityView | null>(() => row.value?.opportunity ?? null)
const displayedStatus = computed<OpportunityViewStatus>(() => opportunity.value?.status ?? row.value?.status ?? 'unavailable')
const isHistoricalOpportunity = computed(() => ['invalidated', 'expired'].includes(displayedStatus.value))
const hasCurrentPlan = computed(() => Boolean(opportunity.value) && [
  'active',
  'preparing',
  'triggered_unverified',
  'in_entry_zone',
  'extended',
  'waiting',
].includes(displayedStatus.value))
const displayedStatusLabel = computed(() => statusLabels[displayedStatus.value] ?? displayedStatus.value)
const currentStatusLabel = computed(() => {
  if (hasCurrentPlan.value || isHistoricalOpportunity.value || ['stale', 'unavailable'].includes(displayedStatus.value)) {
    return displayedStatusLabel.value
  }
  return '当前无有效突破计划'
})
const statusTone = computed(() => {
  if (['invalidated', 'expired', 'unavailable', 'stale'].includes(displayedStatus.value)) return 'negative'
  if (['waiting', 'preparing', 'triggered_unverified', 'extended'].includes(displayedStatus.value)) return 'warning'
  if (displayedStatus.value === 'in_entry_zone') return 'positive'
  return 'neutral'
})
const referencePrice = computed(() => {
  if (!row.value) return null
  if (typeof row.value.reference?.price === 'number' && Number.isFinite(row.value.reference.price)) {
    return row.value.reference.price
  }
  const value = opportunity.value?.reference_price
  return typeof value === 'number' && Number.isFinite(value) ? value : null
})
const referenceKind = computed<OpportunityViewReferenceKind>(() => row.value?.reference?.kind ?? opportunity.value?.reference_kind ?? 'none')
const marketAsOf = computed(() => row.value?.reference?.market_as_of ?? opportunity.value?.market_as_of ?? null)
const qualityStatus = computed(() => row.value?.quality?.status ?? opportunity.value?.quality ?? 'unknown')
const qualityLabel = computed(() => qualityLabels[qualityStatus.value] ?? qualityStatus.value)
const rawQualityWarnings = computed(() => uniqueStrings([
  ...(row.value?.quality?.warnings ?? []),
  ...(opportunity.value?.quality_warnings ?? []),
]))
const rawReasonCodes = computed(() => uniqueStrings([
  ...(row.value?.reason_codes ?? []),
  ...(row.value?.scan?.rejection_codes ?? []),
  ...(opportunity.value?.reason_codes ?? []),
]))
const topWarnings = computed(() => uniqueStrings(response.value?.warnings ?? []))
const scopedTopWarnings = computed(() => {
  const symbol = normalizedSymbol.value
  return topWarnings.value.filter((warning) => {
    const separator = warning.indexOf(':')
    if (separator <= 0) return true
    return normalizeSymbol(warning.slice(0, separator)) === symbol
  })
})
const visibleTopWarnings = computed(() => scopedTopWarnings.value.filter(isUserFacingWarning))
const otherSymbolWarningCount = computed(() => {
  const symbol = normalizedSymbol.value
  return new Set(
    topWarnings.value
      .map((warning) => warning.indexOf(':') > 0 ? normalizeSymbol(warning.slice(0, warning.indexOf(':'))) : '')
      .filter((warningSymbol) => warningSymbol && warningSymbol !== symbol)
      .filter((warningSymbol) => topWarnings.value.some((warning) => {
        const separator = warning.indexOf(':')
        return separator > 0 && normalizeSymbol(warning.slice(0, separator)) === warningSymbol && isUserFacingWarning(warning.slice(separator + 1))
      })),
  ).size
})
const visibleQualityWarnings = computed(() => uniqueStrings([
  ...rawQualityWarnings.value.filter(isUserFacingWarning),
  ...visibleTopWarnings.value,
]))
const technicalWarningCount = computed(() => uniqueStrings([
  ...rawQualityWarnings.value.filter((warning) => !isUserFacingWarning(warning)),
  ...scopedTopWarnings.value.filter((warning) => !isUserFacingWarning(warning)),
]).length)
const freshnessLabel = computed(() => {
  const freshness = opportunity.value?.freshness ?? 'unknown'
  return freshnessLabels[freshness] ?? freshness
})
const evidenceKnowledgeAt = computed(() => response.value?.knowledge_at ?? opportunity.value?.knowledge_at ?? null)
const viewedAt = computed(() => response.value?.viewed_at ?? opportunity.value?.viewed_at ?? null)
const isDailyReference = computed(() => referenceKind.value === 'daily_close')
const latestCloseDate = computed(() => formatDateOnly(marketAsOf.value))
const setupObservedAt = computed(() => opportunity.value?.setup_observed_at ?? row.value?.setup_observed_at ?? null)
const setupValidFrom = computed(() => opportunity.value?.setup_valid_from ?? row.value?.setup_valid_from ?? null)
const setupInvalidatedAt = computed(() => opportunity.value?.setup_invalidated_at ?? row.value?.setup_invalidated_at ?? null)
const currentConclusion = computed(() => {
  if (state.value !== 'success' || !row.value) return '正在读取最新收盘证据…'
  if (!hasCurrentPlan.value) {
    if (isHistoricalOpportunity.value) return '当前无有效突破计划'
    return '当前没有可用突破计划'
  }
  switch (displayedStatus.value) {
    case 'preparing': return '等待突破确认'
    case 'triggered_unverified': return '突破条件待验证'
    case 'in_entry_zone': return '参考价位于研究区间'
    case 'extended': return '价格已超出研究区间'
    case 'waiting': return '等待价格重新验证'
    case 'stale': return '日线证据已过时'
    default: return displayedStatusLabel.value
  }
})
const currentConclusionDetail = computed(() => {
  if (state.value !== 'success' || !row.value) return '本页只读取已冻结的日线证据。'
  if (!hasCurrentPlan.value) {
    return isHistoricalOpportunity.value
      ? '最近一次突破计划已经结束，已移到下方“历史计划与复盘”。这只表示当前没有符合该策略的候选。'
      : '当前没有符合该突破策略的有效候选；这只表示本策略暂无计划。'
  }
  switch (displayedStatus.value) {
    case 'preparing': return '价格尚未完成该策略要求的突破，继续等待后续日线确认。'
    case 'triggered_unverified': return '已有触发迹象，但仅凭当前日线收盘不能确认盘中参与条件。'
    case 'in_entry_zone': return '参考价落在研究区间内，可继续核验相关证据。'
    case 'extended': return '价格已超过研究上限，当前计划不适合追价。'
    case 'waiting': return '触发后的价格暂未回到研究区间，等待重新验证。'
    case 'stale': return '需要新的完成收盘证据后才能判断当前状态。'
    default: return opportunity.value?.condition ?? '按当前日线证据继续观察。'
  }
})
const headerDescription = computed(() => {
  if (state.value !== 'success' || !row.value) return '读取已冻结日线证据，显示当前候选与历史复盘。'
  return `截至 ${latestCloseDate.value} 收盘，以下内容基于已冻结日线证据。`
})
const statusDetail = computed(() => isHistoricalOpportunity.value ? '历史详情见下方复盘' : `日线证据新鲜度：${freshnessLabel.value}`)
const stateLabel = computed(() => {
  if (state.value === 'loading') return '读取中'
  if (state.value === 'error') return '读取失败'
  if (state.value === 'unknown') return '未知'
  return currentStatusLabel.value
})
const unknownMessage = computed(() => {
  if (props.disabled) return '当前证据尚未就绪或为演示数据，未读取机会视图。'
  if (!normalizedDatasetId.value) return '暂无冻结 Daily Dataset，无法读取日线研究计划。'
  if (!normalizedSymbol.value) return '未选择股票代码，无法读取机会视图。'
  return `冻结数据集中没有 ${normalizedSymbol.value} 的机会视图记录。`
})
const reasonCodes = computed(() => rawReasonCodes.value.filter(isUserFacingReason))
const unavailableReason = computed(() => {
  if (reasonCodes.value.length) return reasonCodes.value.map(reasonLabel).join('；')
  if (row.value?.scan?.status) return `扫描状态：${row.value.scan.status}`
  return '后端没有给出可用的机会结构。'
})

function normalizeSymbol(value: string | null | undefined): string {
  return typeof value === 'string' ? value.trim().toUpperCase() : ''
}

function uniqueStrings(values: string[]): string[] {
  return [...new Set(values.filter((value): value is string => typeof value === 'string' && value.trim().length > 0))]
}

/**
 * Keep the main card focused on evidence that changes the price decision.
 * Event coverage and provenance are implementation metadata for this view;
 * they are retained only as a count inside the collapsed trace section.
 */
function isUserFacingWarning(value: string): boolean {
  const normalized = value.trim().toLowerCase()
  if (!normalized) return false
  return !(
    normalized.includes('event')
    || normalized.includes('earnings')
    || normalized.includes('calendar')
    || normalized.includes('事件')
    || normalized.includes('财报')
    || normalized.includes('historical_reconstruction')
    || normalized.includes('historical reconstruction')
    || normalized.includes('provenance')
    || normalized.includes('per_bar_')
    || normalized.includes('source_revision')
    || normalized.includes('session_id')
    || normalized.includes('available_at')
    || normalized.includes('research_support')
    || normalized.includes('收益研究')
  )
}

function isUserFacingReason(value: string): boolean {
  const normalized = value.trim().toLowerCase()
  if (!normalized) return false
  if (['event_coverage_unknown', 'event_reference_date_unknown', 'event_risk_unknown', 'historical_reconstruction', 'invalidation_touched', 'setup_wait_window_expired'].includes(normalized)) return false
  return isUserFacingWarning(normalized)
}

function reasonLabel(code: string): string {
  return reasonLabels[code] ?? code
}

function formatPrice(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : '—'
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return '—'
  return value.replace('T', ' ')
}

function formatDateOnly(value: string | null | undefined): string {
  if (!value) return '未提供'
  const match = value.match(/^(\d{4}-\d{2}-\d{2})/)
  return match?.[1] ?? value.replace('T', ' ').split(' ')[0]
}

function referenceKindLabel(kind: OpportunityViewReferenceKind): string {
  if (kind === 'daily_close') return '完整日线收盘参考（非当前报价）'
  if (kind === 'intraday_quote') return '盘中报价参考'
  return '无参考价'
}

function resetView(nextState: PanelState = 'unknown') {
  row.value = null
  response.value = null
  error.value = ''
  state.value = nextState
}

function validateResponse(payload: DailyOpportunityViewResponse, datasetId: string, symbol: string): OpportunityViewRow | null {
  if (!payload || typeof payload !== 'object' || payload.dataset_id !== datasetId || !Array.isArray(payload.views)) {
    throw new Error('机会视图的 dataset_id 或 views 无效。')
  }
  const matching = payload.views.find((item) => normalizeSymbol(item?.symbol) === symbol)
  if (matching?.opportunity) {
    if (normalizeSymbol(matching.opportunity.symbol) !== symbol) {
      throw new Error('机会视图嵌套 opportunity 的 symbol 与当前个股不一致。')
    }
    if (matching.opportunity.dataset_id !== datasetId) {
      throw new Error('机会视图嵌套 opportunity 的 dataset_id 与当前数据集不一致。')
    }
  }
  return matching ?? null
}

async function loadView() {
  const token = ++requestToken.value
  const datasetId = normalizedDatasetId.value
  const symbol = normalizedSymbol.value
  activeRequestController.value?.abort()
  const controller = new AbortController()
  activeRequestController.value = controller
  resetView()

  if (props.disabled || !datasetId || !symbol) {
    activeRequestController.value = null
    return
  }

  state.value = 'loading'
  try {
    const payload = await api.getDailyOpportunityView(datasetId, symbol, controller.signal)
    if (token !== requestToken.value || datasetId !== normalizedDatasetId.value || symbol !== normalizedSymbol.value || props.disabled || controller.signal.aborted) return
    const matching = validateResponse(payload, datasetId, symbol)
    response.value = payload
    if (!matching) {
      state.value = 'unknown'
      return
    }
    if (normalizeSymbol(matching.symbol) !== symbol) {
      throw new Error('机会视图的 symbol 与当前个股不一致。')
    }
    row.value = matching
    state.value = 'success'
  } catch (reason) {
    if (token !== requestToken.value || controller.signal.aborted) return
    resetView('error')
    error.value = reason instanceof Error ? reason.message : '机会视图请求失败。'
  } finally {
    if (activeRequestController.value === controller) activeRequestController.value = null
  }
}

watch(
  () => [props.datasetId, props.symbol, props.disabled],
  () => { void loadView() },
  { immediate: true },
)

onBeforeUnmount(() => {
  requestToken.value += 1
  activeRequestController.value?.abort()
  activeRequestController.value = null
})
</script>

<template>
  <section class="opportunity-view-panel" :data-state="state" :data-tone="statusTone" :data-disabled="props.disabled ? 'true' : 'false'" aria-live="polite">
    <header class="opportunity-view-head">
      <div>
        <span class="section-kicker">READ ONLY · DAILY EVIDENCE</span>
        <h2>当前日线机会</h2>
        <p>{{ headerDescription }}</p>
      </div>
      <span class="opportunity-view-status" :data-tone="statusTone" data-testid="opportunity-view-status">{{ stateLabel }}</span>
    </header>

    <div v-if="state === 'loading'" class="opportunity-view-message" data-testid="opportunity-view-loading">
      <span class="opportunity-view-spinner"></span> 正在读取冻结日线机会视图…
    </div>
    <div v-else-if="state === 'error'" class="opportunity-view-message opportunity-view-error" role="alert" data-testid="opportunity-view-error">
      机会视图读取失败：{{ error }}
    </div>
    <div v-else-if="state === 'unknown'" class="opportunity-view-message" data-testid="opportunity-view-unknown">
      {{ unknownMessage }}
    </div>

    <template v-else-if="row">
      <div class="opportunity-view-identity">
        <span>{{ row.symbol }}</span>
        <small>最新完成收盘：{{ latestCloseDate }}</small>
      </div>

      <div class="opportunity-view-current-summary" :data-tone="hasCurrentPlan ? statusTone : 'negative'" data-testid="opportunity-view-current-summary">
        <div>
          <span>截至最新收盘 {{ latestCloseDate }}</span>
          <strong>{{ currentConclusion }}</strong>
          <p>{{ currentConclusionDetail }}</p>
          <small v-if="!isHistoricalOpportunity && !opportunity" class="opportunity-view-unavailable-reason">
            {{ unavailableReason }}
          </small>
        </div>
      </div>

      <div class="opportunity-view-grid">
        <div class="opportunity-view-fact opportunity-view-fact-status">
          <span>状态</span>
          <strong>{{ currentStatusLabel }}</strong>
          <small>{{ statusDetail }}</small>
        </div>
        <div class="opportunity-view-fact">
          <span>日线参考价</span>
          <strong>{{ formatPrice(referencePrice) }}</strong>
          <small>{{ referenceKindLabel(referenceKind) }} · {{ formatTimestamp(marketAsOf) }}</small>
        </div>
        <div class="opportunity-view-fact">
          <span>日线证据质量</span>
          <strong>{{ qualityLabel }}</strong>
          <small>{{ visibleQualityWarnings.length ? '存在价格证据限制' : '未返回价格证据限制' }}</small>
        </div>
      </div>

      <div v-if="hasCurrentPlan && opportunity" class="opportunity-view-plan" data-testid="opportunity-view-current-plan">
        <div class="opportunity-view-plan-fact">
          <span>研究区间 L → U</span>
          <strong>{{ formatPrice(opportunity.upper_trigger) }} → {{ formatPrice(opportunity.upper_limit) }}</strong>
          <small>ATR {{ formatPrice(opportunity.atr) }}</small>
        </div>
        <div class="opportunity-view-plan-fact">
          <span>S 失效</span>
          <strong>{{ formatPrice(opportunity.invalidation_stop) }}</strong>
          <small>{{ opportunity.invalidation }}</small>
        </div>
        <div class="opportunity-view-plan-copy">
          <span>条件</span>
          <p>{{ opportunity.condition }}</p>
        </div>
      </div>

      <div v-if="isDailyReference" class="opportunity-view-daily-notice" data-testid="opportunity-view-daily-notice">
        日线研究计划只使用已完成收市证据；没有盘中 quote，日线参考价绝不表示当前可买。
      </div>

      <div v-if="visibleQualityWarnings.length" class="opportunity-view-restrictions" data-testid="opportunity-view-quality">
        <div class="opportunity-view-restrictions-label">
          <span>价格证据限制</span>
          <small v-if="otherSymbolWarningCount">另有 {{ otherSymbolWarningCount }} 个标的存在同类限制</small>
        </div>
        <ul>
          <li v-for="warning in visibleQualityWarnings" :key="warning">{{ warning }}</li>
        </ul>
      </div>

      <div v-if="reasonCodes.length" class="opportunity-view-reasons" data-testid="opportunity-view-reasons">
        <span>判断依据</span>
        <div><em v-for="reason in reasonCodes" :key="reason">{{ reasonLabel(reason) }}</em></div>
      </div>

      <details v-if="isHistoricalOpportunity && opportunity" class="opportunity-view-history" data-testid="opportunity-view-history">
        <summary>历史计划与复盘</summary>
        <div class="opportunity-view-history-intro">
          <strong>{{ displayedStatusLabel }}</strong>
          <span>这是一条已经结束的历史计划。</span>
          <p>下面的价格只用于复盘当时的计划，不代表当前可参与条件。</p>
        </div>
        <div class="opportunity-view-history-dates">
          <div><span>形成日</span><strong>{{ formatDateOnly(setupObservedAt) }}</strong></div>
          <div><span>生效日</span><strong>{{ formatDateOnly(setupValidFrom) }}</strong></div>
          <div><span>结束日</span><strong>{{ formatDateOnly(setupInvalidatedAt) }}</strong></div>
        </div>
        <div class="opportunity-view-history-plan">
          <div class="opportunity-view-plan-fact">
            <span>当时的计划区间 L → U</span>
            <strong>{{ formatPrice(opportunity.upper_trigger) }} → {{ formatPrice(opportunity.upper_limit) }}</strong>
            <small>ATR {{ formatPrice(opportunity.atr) }}</small>
          </div>
          <div class="opportunity-view-plan-fact">
            <span>当时的失效线 S</span>
            <strong>{{ formatPrice(opportunity.invalidation_stop) }}</strong>
            <small>{{ opportunity.invalidation }}</small>
          </div>
        </div>
        <p class="opportunity-view-history-outcome">
          <strong>{{ displayedStatus === 'invalidated' ? '失效' : '到期' }}：</strong>
          {{ displayedStatus === 'invalidated' ? opportunity.invalidation : '等待突破的有效期已结束，该计划不再继续观察。' }}
          <span v-if="setupInvalidatedAt">（{{ formatDateOnly(setupInvalidatedAt) }}）</span>
        </p>
      </details>

      <details class="opportunity-view-trace">
        <summary>查看证据追溯</summary>
        <div><span>scan_id</span><code>{{ row.scan.scan_id }}</code></div>
        <div><span>scan 状态</span><code>{{ row.scan.status }}</code></div>
        <div><span>证据 as-of</span><code>{{ formatTimestamp(evidenceKnowledgeAt) }}</code></div>
        <div><span>查看时点</span><code>{{ formatTimestamp(viewedAt) }}</code></div>
        <div v-if="technicalWarningCount"><span>技术备注</span><code>附加追溯标记已收纳（{{ technicalWarningCount }} 条）。</code></div>
      </details>
    </template>
  </section>
</template>

<style scoped>
.opportunity-view-panel { display: grid; gap: 12px; margin-top: 14px; padding: 15px; border: 1px solid var(--decision-line); background: var(--decision-panel); }
.opportunity-view-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 15px; padding-bottom: 11px; border-bottom: 1px solid var(--decision-line-soft); }
.opportunity-view-head h2 { margin-top: 6px; color: var(--decision-text); font-size: 17px; font-weight: 500; }
.opportunity-view-head p { margin-top: 5px; color: var(--decision-muted); font-size: 10px; line-height: 1.45; }
.opportunity-view-status { display: inline-flex; flex: 0 0 auto; padding: 5px 8px; border: 1px solid var(--decision-line); border-radius: 999px; color: var(--decision-muted); background: var(--decision-panel-raised); font: 9px var(--decision-font-mono); }
.opportunity-view-status[data-tone="positive"] { border-color: rgba(196, 122, 80, .55); color: var(--decision-teal); }
.opportunity-view-status[data-tone="warning"] { border-color: rgba(182, 79, 56, .58); color: var(--decision-amber); }
.opportunity-view-status[data-tone="negative"] { border-color: rgba(219, 101, 82, .5); color: var(--decision-red); }
.opportunity-view-message { display: flex; align-items: center; gap: 8px; min-height: 43px; padding: 10px 12px; border: 1px dashed var(--decision-line); color: var(--decision-muted); font-size: 11px; line-height: 1.5; }
.opportunity-view-message.opportunity-view-error { border-style: solid; border-color: rgba(219, 101, 82, .4); color: var(--decision-red); background: var(--decision-red-soft); }
.opportunity-view-spinner { width: 10px; height: 10px; border: 1px solid var(--decision-amber); border-top-color: transparent; border-radius: 50%; animation: opportunity-view-spin 1s linear infinite; }
.opportunity-view-identity { display: flex; align-items: center; gap: 9px; color: var(--decision-text); font: 600 11px var(--decision-font-mono); }
.opportunity-view-identity small { color: var(--decision-muted); font: 9px var(--decision-font-mono); font-weight: 400; }
.opportunity-view-current-summary { display: flex; align-items: center; justify-content: space-between; gap: 15px; padding: 13px 14px; border: 1px solid var(--decision-line-soft); background: rgba(42, 33, 28, .46); }
.opportunity-view-current-summary[data-tone="negative"] { border-color: rgba(219, 101, 82, .42); background: var(--decision-red-soft); }
.opportunity-view-current-summary > div { min-width: 0; }
.opportunity-view-current-summary > div > span { display: block; color: var(--decision-muted); font: 8px var(--decision-font-mono); letter-spacing: .06em; text-transform: uppercase; }
.opportunity-view-current-summary strong { display: block; margin-top: 6px; color: var(--decision-text); font-size: 15px; font-weight: 600; }
.opportunity-view-current-summary[data-tone="negative"] strong { color: var(--decision-red); }
.opportunity-view-current-summary p { margin-top: 5px; color: var(--decision-soft); font-size: 11px; line-height: 1.5; }
.opportunity-view-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1px; border: 1px solid var(--decision-line-soft); background: var(--decision-line-soft); }
.opportunity-view-fact { min-width: 0; padding: 10px; background: var(--decision-panel-soft); }
.opportunity-view-fact > span, .opportunity-view-plan-fact > span, .opportunity-view-plan-copy > span, .opportunity-view-restrictions > span, .opportunity-view-restrictions-label > span, .opportunity-view-reasons > span { display: block; color: var(--decision-muted); font: 8px var(--decision-font-mono); letter-spacing: .06em; text-transform: uppercase; }
.opportunity-view-fact strong { display: block; margin-top: 6px; overflow: hidden; color: var(--decision-soft); font: 500 12px var(--decision-font-mono); text-overflow: ellipsis; white-space: nowrap; }
.opportunity-view-fact small { display: block; margin-top: 4px; overflow: hidden; color: var(--decision-muted); font: 8px/1.4 var(--decision-font-mono); text-overflow: ellipsis; white-space: nowrap; }
.opportunity-view-fact-status strong { color: var(--decision-amber); }
.opportunity-view-panel[data-tone="positive"] .opportunity-view-fact-status strong { color: var(--decision-teal); }
.opportunity-view-panel[data-tone="negative"] .opportunity-view-fact-status strong { color: var(--decision-red); }
.opportunity-view-plan { display: grid; grid-template-columns: minmax(140px, .7fr) minmax(140px, .7fr) minmax(220px, 1.6fr); gap: 1px; border: 1px solid var(--decision-line-soft); background: var(--decision-line-soft); }
.opportunity-view-plan-fact, .opportunity-view-plan-copy { min-width: 0; padding: 10px; background: rgba(42, 33, 28, .36); }
.opportunity-view-plan-fact strong { display: block; margin-top: 6px; color: var(--decision-text); font: 500 14px var(--decision-font-mono); }
.opportunity-view-plan-fact small { display: block; margin-top: 4px; color: var(--decision-muted); font: 8px/1.4 var(--decision-font-mono); }
.opportunity-view-plan-copy p { margin-top: 6px; color: var(--decision-soft); font-size: 11px; line-height: 1.45; }
.opportunity-view-unavailable, .opportunity-view-daily-notice, .opportunity-view-restrictions, .opportunity-view-reasons { padding: 10px 12px; border: 1px solid var(--decision-line-soft); background: rgba(28, 23, 20, .5); }
.opportunity-view-unavailable { border-color: rgba(219, 101, 82, .33); background: var(--decision-red-soft); }
.opportunity-view-unavailable strong { color: var(--decision-red); font-size: 11px; }
.opportunity-view-unavailable p { margin-top: 5px; color: var(--decision-soft); font-size: 11px; line-height: 1.45; }
.opportunity-view-unavailable-reason { display: block; margin-top: -4px; color: var(--decision-muted); font-size: 9px; line-height: 1.4; }
.opportunity-view-daily-notice { color: var(--decision-amber); font-size: 10px; line-height: 1.45; }
.opportunity-view-restrictions { display: grid; grid-template-columns: 150px minmax(0, 1fr); gap: 10px; color: var(--decision-amber); font-size: 10px; line-height: 1.45; }
.opportunity-view-restrictions-label { display: grid; align-content: start; gap: 4px; }
.opportunity-view-restrictions-label small { color: var(--decision-muted); font-size: 8px; line-height: 1.35; }
.opportunity-view-restrictions ul { display: grid; gap: 4px; margin: 0; padding-left: 16px; color: var(--decision-soft); }
.opportunity-view-reasons { display: grid; grid-template-columns: 75px minmax(0, 1fr); gap: 10px; }
.opportunity-view-reasons > div { display: flex; flex-wrap: wrap; gap: 5px; }
.opportunity-view-reasons em { padding: 3px 5px; border: 1px solid var(--decision-line); border-radius: 3px; color: var(--decision-muted); font: 8px var(--decision-font-mono); font-style: normal; }
.opportunity-view-history { border: 1px solid var(--decision-line-soft); color: var(--decision-muted); font: 9px var(--decision-font-mono); }
.opportunity-view-history summary { padding: 10px 12px; cursor: pointer; color: var(--decision-soft); }
.opportunity-view-history[open] summary { border-bottom: 1px solid var(--decision-line-soft); }
.opportunity-view-history-intro { padding: 10px 12px 0; }
.opportunity-view-history-intro strong { color: var(--decision-amber); font-size: 11px; }
.opportunity-view-history-intro > span { margin-left: 7px; color: var(--decision-muted); font-size: 10px; }
.opportunity-view-history-intro p { margin-top: 4px; color: var(--decision-muted); font-size: 10px; line-height: 1.45; }
.opportunity-view-history-dates { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1px; margin: 10px 12px 0; border: 1px solid var(--decision-line-soft); background: var(--decision-line-soft); }
.opportunity-view-history-dates > div { padding: 9px; background: rgba(42, 33, 28, .36); }
.opportunity-view-history-dates span { display: block; color: var(--decision-muted); font-size: 8px; }
.opportunity-view-history-dates strong { display: block; margin-top: 5px; color: var(--decision-soft); font: 500 11px var(--decision-font-mono); }
.opportunity-view-history-plan { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 1px; margin: 10px 12px 0; border: 1px solid var(--decision-line-soft); background: var(--decision-line-soft); }
.opportunity-view-history-outcome { margin: 10px 12px 12px; color: var(--decision-soft); font-size: 10px; line-height: 1.5; }
.opportunity-view-history-outcome strong { color: var(--decision-red); }
.opportunity-view-trace { border-top: 1px solid var(--decision-line-soft); color: var(--decision-muted); font: 9px var(--decision-font-mono); }
.opportunity-view-trace summary { width: fit-content; padding-top: 10px; cursor: pointer; }
.opportunity-view-trace > div { display: flex; flex-wrap: wrap; gap: 7px 12px; margin-top: 7px; }
.opportunity-view-trace code { color: var(--decision-soft); font: inherit; word-break: break-all; }
@keyframes opportunity-view-spin { to { transform: rotate(360deg); } }

@media (max-width: 1050px) {
  .opportunity-view-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (max-width: 680px) {
  .opportunity-view-head { flex-direction: column; }
  .opportunity-view-grid, .opportunity-view-plan { grid-template-columns: 1fr 1fr; }
  .opportunity-view-plan-copy { grid-column: 1 / -1; }
}
@media (max-width: 430px) {
  .opportunity-view-grid, .opportunity-view-plan { grid-template-columns: 1fr; }
  .opportunity-view-plan-copy { grid-column: auto; }
  .opportunity-view-restrictions, .opportunity-view-reasons { grid-template-columns: 1fr; gap: 6px; }
  .opportunity-view-history-dates, .opportunity-view-history-plan { grid-template-columns: 1fr; }
}
</style>
