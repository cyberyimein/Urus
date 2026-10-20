<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { getIntradayEventHealth, getIntradayEvents } from '@/api/intraday'
import type {
  IntradayEvent,
  IntradayEventHealth,
  IntradayFrozenLevel,
} from '@/types/intradayEvents'

const props = defineProps<{
  selectedSymbol: string
  tradingDate: string
  refreshToken: number
}>()

const emit = defineEmits<{
  (event: 'frozen-levels', levels: IntradayFrozenLevel[]): void
}>()

const events = ref<IntradayEvent[]>([])
const health = ref<IntradayEventHealth | null>(null)
const symbolFilter = ref('')
const strategyFilter = ref('')
const statusFilter = ref('')
const initialLoading = ref(true)
const error = ref('')
let controller: AbortController | null = null
let generation = 0

const activeFrozenLevels = computed<IntradayFrozenLevel[]>(() => {
  const latestBySetup = new Map<string, IntradayEvent>()
  for (const item of events.value) {
    if (item.symbol !== props.selectedSymbol || item.trading_date !== props.tradingDate) continue
    const current = latestBySetup.get(item.setup_id)
    if (!current || item.id > current.id) latestBySetup.set(item.setup_id, item)
  }
  return [...latestBySetup.values()]
    .filter((item) => item.current_status === 'candidate' || item.current_status === 'confirmed')
    .map((item) => ({
      setupId: item.setup_id,
      symbol: item.symbol,
      tradingDate: item.trading_date,
      setupType: item.setup_type,
      currentStatus: item.current_status,
      confirmationLevel: item.confirmation_level,
      invalidationLevel: item.invalidation_level,
    }))
})

const consumerLabel = computed(() => {
  if (!health.value) return '正在读取事件状态'
  if (!health.value.enabled || health.value.status === 'disabled') return '事件功能已关闭'
  if (health.value.status === 'error') return '事件处理异常'
  if (health.value.status === 'idle' && !health.value.last_evaluated_at) return '已启用，等待首次处理'
  if (health.value.stale) return '最近处理已停止更新'
  return '事件处理最近有更新'
})

function formatEastern(value: string | null | undefined): string {
  if (!value) return '—'
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

function ageLabel(seconds: number | null | undefined): string {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return '—'
  if (seconds < 60) return `${Math.round(seconds)} 秒前`
  return `${Math.floor(seconds / 60)} 分钟前`
}

function staleThresholdMinutes(seconds: number | null | undefined): number {
  const normalized = typeof seconds === 'number' && Number.isFinite(seconds) && seconds > 0
    ? seconds
    : 600
  return Math.ceil(normalized / 60)
}

function eventStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    candidate: '候选',
    confirmed: '已确认',
    invalidated: '已失效',
    expired: '已过期',
  }
  return labels[status] ?? status
}

function setupTypeLabel(setupType: string): string {
  const labels: Record<string, string> = {
    relative_strength_improving: '相对强度改善',
    relative_strength_weakening: '相对强度减弱',
    relative_strength_recovering: '弱势修复',
    relative_strength_worsening: '弱势延续',
    ma5_reclaim: '站回 MA5',
    ma5_break: '失守 MA5',
    ma5_retest_hold: 'MA5 回踩守住',
    ma5_failed_reclaim: 'MA5 反抽失败',
    ma5_extension_risk: 'MA5 乖离扩大',
    price_anomaly_drop: '异常急跌',
    price_impulse_up: '放量拉升',
    price_impulse_down: '放量下挫',
    orb_breakout: '开盘区间突破',
    range_breakout: '区间突破／延续',
    v_reversal: 'V 形回升',
    a_reversal: 'A 形回落',
    failed_breakout: '假突破失败',
    simplified_setup_exhaustion: '当日简化九转',
  }
  return labels[setupType] ?? setupType
}

function strategyLabel(strategy: string): string {
  const labels: Record<string, string> = {
    S1: 'S1 相对强度',
    S2: 'S2 MA5',
    S3: 'S3 异动',
    S4: 'S4 开盘区间',
    S5: 'S5 区间突破',
    S6: 'S6 A/V 结构',
    S7: 'S7 假突破',
    S8: 'S8 简化九转',
  }
  return labels[strategy] ?? strategy
}

function frozenLevelSummary(level: IntradayFrozenLevel): string {
  const confirmation = level.confirmationLevel === null ? '—' : level.confirmationLevel.toFixed(2)
  const invalidation = level.invalidationLevel === null ? '—' : level.invalidationLevel.toFixed(2)
  return `冻结确认线 ${confirmation} · 冻结失效线 ${invalidation}`
}

function buildFilters() {
  return {
    symbol: symbolFilter.value.trim() || undefined,
    strategy: strategyFilter.value || undefined,
    status: statusFilter.value || undefined,
    limit: 50,
  }
}

async function poll() {
  const currentGeneration = ++generation
  controller?.abort()
  controller = new AbortController()
  const signal = controller.signal
  error.value = ''
  try {
    const [eventResponse, healthResponse] = await Promise.all([
      getIntradayEvents(buildFilters(), signal),
      getIntradayEventHealth(signal),
    ])
    if (currentGeneration !== generation || signal.aborted) return
    health.value = healthResponse
    if (eventResponse.status === 'error') {
      error.value = eventResponse.error ?? '事件读取失败。'
    }
    events.value = eventResponse.events
  } catch (reason) {
    if (currentGeneration === generation && !signal.aborted) {
      error.value = reason instanceof Error ? reason.message : '事件读取失败。'
      events.value = []
    }
  } finally {
    if (currentGeneration === generation) initialLoading.value = false
  }
}

watch([symbolFilter, strategyFilter, statusFilter], () => {
  events.value = []
  void poll()
})

watch(activeFrozenLevels, (levels) => emit('frozen-levels', levels), { immediate: true })
watch(() => props.refreshToken, () => { void poll() })
watch(() => [props.selectedSymbol, props.tradingDate], () => {
  emit('frozen-levels', activeFrozenLevels.value)
})

onMounted(() => {
  void poll()
})

onBeforeUnmount(() => {
  controller?.abort()
})
</script>

<template>
  <section class="intraday-event-panel" aria-label="盘中事件">
    <header class="intraday-event-heading">
      <div>
        <span class="section-kicker">INTRADAY SETUPS</span>
        <h2>盘中事件</h2>
      </div>
      <div class="intraday-event-health" :data-status="health?.status ?? 'idle'">
        <span class="intraday-event-dot" aria-hidden="true"></span>
        <span>{{ consumerLabel }}</span>
        <small v-if="health?.last_evaluated_at">
          最近处理 {{ formatEastern(health.last_evaluated_at) }} ET · {{ ageLabel(health.age_seconds) }} · 陈旧阈值 {{ staleThresholdMinutes(health.stale_after_seconds) }} 分钟
        </small>
        <small v-else-if="health?.status === 'disabled'">只读事件展示，不会启动处理器。</small>
        <small v-if="health?.backlog_remaining">仍有源槽待处理</small>
        <small v-if="health?.last_error">{{ health.last_error }}</small>
      </div>
    </header>

    <div class="intraday-event-filters" aria-label="筛选盘中事件">
      <label><span>标的</span><input v-model="symbolFilter" type="search" placeholder="全部标的" aria-label="筛选事件标的"></label>
      <label>
        <span>策略</span>
        <select v-model="strategyFilter" aria-label="筛选事件策略">
          <option value="">全部策略</option>
          <option value="S1">S1 · 相对强度</option>
          <option value="S2">S2 · MA5</option>
          <option value="S3">S3 · 异动</option>
          <option value="S4">S4 · 开盘区间</option>
          <option value="S5">S5 · 区间突破</option>
          <option value="S6">S6 · A/V 结构</option>
          <option value="S7">S7 · 假突破</option>
          <option value="S8">S8 · 简化九转</option>
        </select>
      </label>
      <label><span>状态</span><select v-model="statusFilter" aria-label="筛选事件状态"><option value="">全部状态</option><option value="candidate">候选</option><option value="confirmed">已确认</option><option value="invalidated">已失效</option><option value="expired">已过期</option></select></label>
      <small>当前筛选下最近 50 条，与行情一起每 5 分钟整点读取。</small>
    </div>

    <p v-if="error" class="intraday-event-error" role="alert">{{ error }}</p>
    <div v-if="initialLoading && !events.length" class="intraday-event-empty">正在读取事件…</div>
    <div v-else-if="!events.length" class="intraday-event-empty">
      {{ health?.status === 'disabled' ? '事件消费者未启用。' : '当前筛选没有事件。' }}
    </div>
    <div v-else class="intraday-event-list">
      <article v-for="item in [...events].reverse()" :key="item.id" class="intraday-event-row" :data-status="item.status">
        <div class="intraday-event-main">
          <div class="intraday-event-title">
            <strong>{{ item.symbol }}</strong>
            <span>{{ strategyLabel(item.strategy) }} · {{ setupTypeLabel(item.setup_type) }}</span>
            <span class="intraday-event-status" :data-status="item.status">{{ eventStatusLabel(item.status) }}</span>
          </div>
          <p>{{ item.message || item.reason_code }}</p>
          <small>{{ formatEastern(item.evidence_cutoff) }} ET · {{ item.trading_date }} · 当前 {{ eventStatusLabel(item.current_status) }}</small>
        </div>
        <details class="intraday-event-details">
          <summary>依据与冻结线</summary>
          <p v-if="item.confirmation_level !== null || item.invalidation_level !== null">
            确认线 {{ item.confirmation_level?.toFixed(2) ?? '—' }} · 失效线 {{ item.invalidation_level?.toFixed(2) ?? '—' }}
          </p>
          <small>策略 ID {{ item.strategy_id }} · 类型 ID {{ item.setup_type }}</small>
          <pre>{{ JSON.stringify(item.evidence, null, 2) }}</pre>
          <small>Capture {{ item.source_capture_id || '—' }} · {{ item.rule_version }}</small>
        </details>
      </article>
    </div>

    <div v-if="activeFrozenLevels.length" class="intraday-event-lines" aria-label="所选标的同日冻结线">
      <small>仅当前标的 {{ selectedSymbol }} / {{ tradingDate }} 图表使用冻结线。</small>
      <span v-for="level in activeFrozenLevels" :key="level.setupId">
        {{ setupTypeLabel(level.setupType) }} · {{ eventStatusLabel(level.currentStatus) }} · {{ frozenLevelSummary(level) }}
      </span>
    </div>
  </section>
</template>
