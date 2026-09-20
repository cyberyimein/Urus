<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import { api } from '@/api/client'
import AppShell from '@/components/AppShell.vue'
import type { ObservationRun } from '@/types/api'
import type {
  DiscoveryEvent,
  DiscoveryProjection,
  DiscoveryValue,
  GroupPhaseChangeEvent,
  IndicatorTransitionEvent,
} from '@/types/discovery'

const loading = ref(true)
const error = ref('')
const selectedRun = ref<ObservationRun | null>(null)
const discovery = ref<DiscoveryProjection | null>(null)
type FocusFilter = 'all' | 'focused' | 'unfocused'

const focusFilter = ref<FocusFilter>('all')
const focusSymbols = ref(new Set<string>())
const focusLoading = ref(false)
const focusLoaded = ref(false)
const focusAvailable = ref(false)
const focusError = ref('')
const focusRetryMode = ref<'load' | 'toggle' | null>(null)
const focusRetrySymbol = ref('')
const pendingFocusSymbol = ref('')
const ALL_INDICATORS = '__all__'

const events = computed(() => (discovery.value?.events ?? []).filter(
  (event): event is IndicatorTransitionEvent => event.kind === 'indicator_transition',
))
const groupEvents = computed(() => (discovery.value?.events ?? []).filter(
  (event): event is GroupPhaseChangeEvent => event.kind === 'group_phase_change',
))
const indicatorOptions = computed(() => {
  const options = new Map<string, { id: string; name: string; count: number }>()
  for (const event of events.value) {
    const existing = options.get(event.lens.id)
    if (existing) {
      existing.count += 1
    } else {
      options.set(event.lens.id, { id: event.lens.id, name: event.lens.name, count: 1 })
    }
  }
  return [...options.values()]
})
const selectedIndicatorId = ref('')
const indicatorMenu = ref<HTMLDetailsElement | null>(null)
const indicatorMenuOpen = ref(false)
watch(indicatorOptions, (options) => {
  if (!options.length) {
    selectedIndicatorId.value = ''
    return
  }
  if (selectedIndicatorId.value !== ALL_INDICATORS && !options.some((option) => option.id === selectedIndicatorId.value)) {
    selectedIndicatorId.value = options[0].id
  }
}, { immediate: true })
const selectedIndicatorOption = computed(() => {
  if (selectedIndicatorId.value === ALL_INDICATORS) {
    return { id: ALL_INDICATORS, name: '全部指标', count: events.value.length }
  }
  return indicatorOptions.value.find((option) => option.id === selectedIndicatorId.value)
    ?? indicatorOptions.value[0]
    ?? null
})
const selectedIndicatorEvents = computed(() => (
  selectedIndicatorId.value === ALL_INDICATORS
    ? events.value
    : events.value.filter((event) => event.lens.id === selectedIndicatorId.value)
))
const totalEvents = computed(() => events.value.length + groupEvents.value.length)
const focusStateKnown = computed(() => focusLoaded.value && focusAvailable.value)
const filteredEvents = computed(() => {
  if (!focusStateKnown.value || focusFilter.value === 'all') return selectedIndicatorEvents.value
  return selectedIndicatorEvents.value.filter((event) => (
    focusFilter.value === 'focused'
      ? isFocused(event.symbol)
      : !isFocused(event.symbol)
  ))
})
const currentRunId = computed(() => discovery.value?.run.run_id ?? selectedRun.value?.run_id ?? '')
const compareDate = computed(() => {
  const comparison = discovery.value?.comparison
  if (!comparison) return '—'
  if (comparison.previous_trading_date) return comparison.previous_trading_date
  if (comparison.previous_trading_dates.length > 1) return `${comparison.previous_trading_dates.length} 个日期`
  return '无可比历史'
})
const qualityLabel = computed(() => {
  const status = discovery.value?.quality.status
  if (status === 'partial') return '部分可用'
  if (status === 'ok') return '完整'
  return status || '—'
})
const warnings = computed(() => discovery.value?.quality.warnings ?? [])
const noComparableHistory = computed(() => discovery.value?.comparison.status === 'unavailable')

function isReadyRun(run: ObservationRun) {
  return run.status === 'succeeded' || run.status === 'mixed'
}

function runSortKey(run: ObservationRun) {
  return [run.trading_date, run.completed_at ?? '', run.created_at, run.run_id].join('|')
}

function statusLabel(status: string) {
  if (status === 'succeeded') return '已完成'
  if (status === 'mixed') return '部分完成'
  return status || '未知'
}

function dateLabel(value: string | null | undefined) {
  return value || '—'
}

function valueLabel(value: DiscoveryValue) {
  const state = value.state_label || value.state || ''
  const display = value.display_value || (value.value === null || value.value === undefined ? '' : String(value.value))
  if (state && display && state !== display) return `${state} · ${display}`
  return state || display || '—'
}

function ratioLabel(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : '—'
}

function signedRatioLabel(value: number | null | undefined) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return `${value >= 0 ? '+' : ''}${(value * 100).toFixed(1)}%`
}

function percentLabel(value: number | null | undefined) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
}

function countLabel(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? String(value) : '—'
}

function groupsLabel(event: DiscoveryEvent) {
  return event.groups.map((group) => group.name).filter(Boolean)
}

function normalizeSymbol(symbol: string) {
  return symbol.trim().toUpperCase()
}

function isFocused(symbol: string) {
  return focusStateKnown.value && focusSymbols.value.has(normalizeSymbol(symbol))
}

function focusFilterLabel(filter: FocusFilter) {
  if (filter === 'focused') return '我的重点'
  if (filter === 'unfocused') return '未重点关注'
  return '全部'
}

function selectIndicator(id: string) {
  selectedIndicatorId.value = id
  indicatorMenu.value?.removeAttribute('open')
  indicatorMenuOpen.value = false
}

function handleIndicatorMenuToggle(event: Event) {
  indicatorMenuOpen.value = (event.currentTarget as HTMLDetailsElement).open
}

function closeIndicatorMenu(event: KeyboardEvent) {
  if (event.key !== 'Escape') return
  event.preventDefault()
  const details = event.currentTarget as HTMLDetailsElement
  details.open = false
  indicatorMenuOpen.value = false
  details.querySelector<HTMLElement>('summary')?.focus()
}

async function loadFocusSymbols() {
  focusLoading.value = true
  focusLoaded.value = false
  focusAvailable.value = false
  focusFilter.value = 'all'
  focusSymbols.value = new Set()
  focusError.value = ''
  focusRetryMode.value = null
  try {
    const items = await api.listFocusSymbols()
    focusSymbols.value = new Set(items.map((item) => normalizeSymbol(item.symbol)))
    focusAvailable.value = true
  } catch (reason) {
    focusError.value = reason instanceof Error ? reason.message : '重点关注列表加载失败。'
    focusRetryMode.value = 'load'
  } finally {
    focusLoaded.value = true
    focusLoading.value = false
  }
}

async function toggleFocus(symbol: string) {
  const normalized = normalizeSymbol(symbol)
  if (pendingFocusSymbol.value) return
  const removing = focusSymbols.value.has(normalized)
  pendingFocusSymbol.value = normalized
  focusError.value = ''
  focusRetryMode.value = null
  focusRetrySymbol.value = normalized
  try {
    if (removing) {
      await api.removeFocusSymbol(normalized)
    } else {
      await api.addFocusSymbol(normalized)
    }
    const next = new Set(focusSymbols.value)
    if (removing) next.delete(normalized)
    else next.add(normalized)
    focusSymbols.value = next
  } catch (reason) {
    focusError.value = reason instanceof Error ? reason.message : '重点关注操作失败。'
    focusRetryMode.value = 'toggle'
  } finally {
    pendingFocusSymbol.value = ''
  }
}

async function retryFocusAction() {
  if (focusRetryMode.value === 'toggle' && focusRetrySymbol.value) {
    await toggleFocus(focusRetrySymbol.value)
    return
  }
  await loadFocusSymbols()
}

async function load() {
  loading.value = true
  error.value = ''
  selectedRun.value = null
  discovery.value = null

  try {
    const [runItems] = await Promise.all([
      api.listObservationRuns(30),
      loadFocusSymbols(),
    ])
    const readyRuns = runItems
      .filter(isReadyRun)
      .sort((left, right) => runSortKey(right).localeCompare(runSortKey(left)))
    const latestReadyRun = readyRuns[0] ?? null
    selectedRun.value = latestReadyRun
    if (latestReadyRun) {
      discovery.value = await api.getObservationDiscovery(latestReadyRun.run_id)
    }
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '发现请求失败。'
  } finally {
    loading.value = false
  }
}

onMounted(() => { void load() })
</script>

<template>
  <AppShell />
  <main class="discovery-page page-shell">
    <section class="discovery-hero">
      <div>
        <p class="eyebrow">CHANGE DISCOVERY / OBSERVATION RUN</p>
        <h1>发现值得重新看的变化</h1>
        <p>从最近一次已完成的收盘观测里，先找出板块阶段变化与个股指标状态切换。</p>
      </div>
      <div class="discovery-hero-aside" aria-label="发现范围">
        <span>只读投影</span>
        <strong>最近 30 次观测</strong>
        <small>板块阶段变化 + 个股指标变化</small>
      </div>
    </section>

    <section v-if="loading" class="state-panel discovery-state" data-testid="discovery-loading">
      <p class="eyebrow">LOADING</p>
      <h2>正在读取发现数据</h2>
      <p>正在检查最近的 Observation Run。</p>
    </section>

    <section v-else-if="error" class="error-banner discovery-state" data-testid="discovery-error">
      <p class="eyebrow">REQUEST FAILED</p>
      <h2>发现数据加载失败</h2>
      <p>{{ error }}</p>
    </section>

    <section v-else-if="!selectedRun || !discovery" class="empty-panel discovery-state" data-testid="discovery-no-run">
      <p class="eyebrow">NO READY OBSERVATION</p>
      <h2>暂无已完成观察</h2>
      <p>最近 30 次 Observation Run 中没有 succeeded 或 mixed 记录，暂时没有可供发现的内容。</p>
    </section>

    <template v-else>
      <section class="discovery-run-strip" aria-label="当前发现运行摘要">
        <div>
          <span>当前日期</span>
          <strong>{{ dateLabel(discovery.run.trading_date) }}</strong>
        </div>
        <div>
          <span>比较日期</span>
          <strong>{{ compareDate }}</strong>
        </div>
        <div>
          <span>运行状态</span>
          <strong>{{ statusLabel(discovery.run.status) }}</strong>
        </div>
        <div>
          <span>变化 / 质量</span>
          <strong>{{ totalEvents }} / {{ qualityLabel }}</strong>
        </div>
      </section>

      <section v-if="focusError" class="discovery-focus-error" data-testid="discovery-focus-error" role="alert">
        <span>{{ focusError }}</span>
        <button type="button" class="secondary-button" :disabled="focusLoading" @click="retryFocusAction">重试</button>
      </section>

      <section v-if="discovery.quality.status === 'partial'" class="notice-box warning-box" data-testid="discovery-partial-quality">
        <strong>部分数据</strong>
        <span>部分来源不可比或投影不可用，以下内容只代表当前可计算的事实。</span>
      </section>

      <details v-if="warnings.length" class="discovery-warnings">
        <summary>数据说明（{{ warnings.length }}）</summary>
        <ul>
          <li v-for="warning in warnings" :key="warning">{{ warning }}</li>
        </ul>
      </details>

      <section v-if="!totalEvents" class="empty-panel discovery-empty" data-testid="discovery-no-events">
        <p class="eyebrow">NO DISCOVERY EVENTS</p>
        <h2>{{ noComparableHistory ? '暂无可比历史' : '本次没有板块或个股变化' }}</h2>
        <p v-if="noComparableHistory">本次运行没有找到前一交易日的冻结数据，因此暂不生成跨日变化。</p>
        <p v-else>当前运行已经完成，但没有发现满足条件的板块阶段变化或个股指标状态切换。</p>
      </section>

      <template v-else>
        <section class="discovery-events discovery-group-events" aria-label="板块阶段变化">
          <div class="discovery-section-heading">
            <div>
              <p class="section-kicker">GROUP PHASE CHANGES</p>
              <h2>板块阶段变化</h2>
            </div>
            <small>共 {{ groupEvents.length }} 条</small>
          </div>

          <p v-if="!groupEvents.length" class="discovery-subsection-empty" data-testid="discovery-no-group-events">
            本次没有满足样本门槛的板块阶段变化
          </p>

          <div v-else class="discovery-group-card-grid">
            <article
              v-for="event in groupEvents"
              :key="event.event_id"
              class="discovery-card discovery-group-card"
              :data-event-id="event.event_id"
              data-testid="discovery-group-event-card"
            >
              <header class="discovery-card-head">
                <div>
                  <span class="discovery-symbol">{{ event.group.name }}</span>
                  <h3>{{ event.stage.label }}</h3>
                </div>
                <span class="discovery-kind">板块阶段</span>
              </header>

              <div class="discovery-group-transition" aria-label="板块状态变化前后">
                <span>{{ valueLabel(event.before) }}</span>
                <span class="discovery-arrow" aria-hidden="true">→</span>
                <strong>{{ valueLabel(event.after) }}</strong>
              </div>

              <div class="discovery-group-facts">
                <div>
                  <span>MA20 参与度</span>
                  <strong>{{ ratioLabel(event.metrics.participation ?? event.metrics.breadth_ma20) }}</strong>
                </div>
                <div>
                  <span>较前次广度变化</span>
                  <strong>{{ signedRatioLabel(event.metrics.breadth_ma20_delta) }}</strong>
                </div>
                <div>
                  <span>相对强弱 / 变化</span>
                  <strong>{{ percentLabel(event.metrics.relative_20d) }} / {{ percentLabel(event.metrics.relative_20d_delta) }}</strong>
                </div>
                <div>
                  <span>龙头集中度</span>
                  <strong>{{ ratioLabel(event.metrics.leader_concentration) }}</strong>
                </div>
                <div>
                  <span>有效 / 请求样本</span>
                  <strong>{{ countLabel(event.metrics.valid_symbol_count) }} / {{ countLabel(event.metrics.requested_symbol_count) }} · {{ ratioLabel(event.metrics.coverage) }}</strong>
                </div>
                <div>
                  <span>持续时长</span>
                  <strong>{{ event.duration.label }}</strong>
                </div>
              </div>

              <div v-if="event.metrics.leaders_added.length || event.metrics.leaders_removed.length" class="discovery-leader-changes">
                <div v-if="event.metrics.leaders_added.length">
                  <span>新增龙头</span>
                  <strong>{{ event.metrics.leaders_added.join('、') }}</strong>
                </div>
                <div v-if="event.metrics.leaders_removed.length">
                  <span>退出龙头</span>
                  <strong>{{ event.metrics.leaders_removed.join('、') }}</strong>
                </div>
              </div>

              <div class="discovery-card-actions">
                <RouterLink
                  :to="event.route"
                  class="primary-button"
                  data-testid="group-evidence-link"
                >查看板块证据</RouterLink>
              </div>
            </article>
          </div>
        </section>

        <section class="discovery-events" aria-label="个股指标变化">
          <div class="discovery-section-heading">
            <div>
              <p class="section-kicker">INDICATOR TRANSITIONS</p>
              <h2>个股指标变化</h2>
            </div>
            <small>当前展示 {{ filteredEvents.length }} / 所选指标 {{ selectedIndicatorEvents.length }} / 全部指标 {{ events.length }}</small>
          </div>

          <section v-if="!events.length" class="discovery-subsection-empty" data-testid="discovery-no-indicator-events">
            本次没有个股指标状态变化
          </section>

          <template v-else>
            <div class="discovery-indicator-selector">
              <span class="discovery-indicator-selector-label">指标切换</span>
              <details
                ref="indicatorMenu"
                class="discovery-indicator-menu"
                data-testid="discovery-indicator-menu"
                @keydown="closeIndicatorMenu"
                @toggle="handleIndicatorMenuToggle"
              >
                <summary
                  class="discovery-indicator-trigger"
                  data-testid="discovery-indicator-trigger"
                  aria-label="指标切换"
                  aria-haspopup="listbox"
                  :aria-expanded="indicatorMenuOpen"
                >
                  <span class="discovery-indicator-trigger-copy">
                    <span class="discovery-indicator-trigger-label">{{ selectedIndicatorOption?.name ?? '指标' }}</span>
                    <span class="discovery-indicator-trigger-count">（{{ selectedIndicatorOption?.count ?? 0 }}）</span>
                  </span>
                  <span class="discovery-indicator-chevron" aria-hidden="true">⌄</span>
                </summary>
                <div class="discovery-indicator-popover" role="listbox" aria-label="选择指标">
                  <button
                    type="button"
                    class="discovery-indicator-option"
                    :class="{ active: selectedIndicatorId === ALL_INDICATORS }"
                    :aria-selected="selectedIndicatorId === ALL_INDICATORS"
                    role="option"
                    data-testid="discovery-indicator-option"
                    @click="selectIndicator(ALL_INDICATORS)"
                  >
                    <span>全部指标</span>
                    <span>{{ events.length }}</span>
                  </button>
                  <button
                    v-for="option in indicatorOptions"
                    :key="option.id"
                    type="button"
                    class="discovery-indicator-option"
                    :class="{ active: selectedIndicatorId === option.id }"
                    :aria-selected="selectedIndicatorId === option.id"
                    role="option"
                    data-testid="discovery-indicator-option"
                    @click="selectIndicator(option.id)"
                  >
                    <span>{{ option.name }}</span>
                    <span>{{ option.count }}</span>
                  </button>
                </div>
              </details>
            </div>
            <section class="discovery-event-controls" data-testid="discovery-focus-controls" aria-label="重点关注筛选">
              <div class="discovery-filter-tabs" role="group" aria-label="事件筛选">
                <button
                  v-for="filter in (['all', 'focused', 'unfocused'] as FocusFilter[])"
                  :key="filter"
                  type="button"
                  class="discovery-filter-tab"
                  :class="{ active: focusFilter === filter }"
                  :aria-pressed="focusFilter === filter"
                  :disabled="focusLoading || (filter !== 'all' && !focusStateKnown)"
                  @click="focusFilter = filter"
                >{{ focusFilterLabel(filter) }}</button>
              </div>
            </section>

            <section v-if="!filteredEvents.length" class="empty-panel discovery-empty" data-testid="discovery-filter-empty">
              <p class="eyebrow">NO MATCHING FOCUS EVENTS</p>
              <h2>{{ focusFilterLabel(focusFilter) }}暂无变化</h2>
              <p v-if="focusFilter === 'focused' && !focusSymbols.size">当前还没有个人重点关注记录；请选择“全部”或先在卡片上加入重点。</p>
              <p v-else>当前筛选没有匹配的指标状态变化。</p>
            </section>

            <section v-else class="discovery-indicator-events">
        <div class="discovery-card-grid">
          <article
            v-for="event in filteredEvents"
            :key="event.event_id"
            class="discovery-card"
            :data-event-id="event.event_id"
            data-testid="discovery-event-card"
          >
            <header class="discovery-card-head">
              <div>
                <span class="discovery-symbol">{{ event.symbol }}</span>
                <h3>{{ event.lens.name }}</h3>
              </div>
              <div class="discovery-card-badges">
                <span class="discovery-kind">指标变化</span>
                <span class="discovery-focus-status" :data-focused="focusStateKnown ? isFocused(event.symbol) : 'unknown'">
                  {{ !focusStateKnown ? '关注状态未知' : isFocused(event.symbol) ? '重点关注' : '未重点' }}
                </span>
              </div>
            </header>

            <div class="discovery-transition" aria-label="指标状态变化前后">
              <div class="discovery-transition-state">
                <span>之前 · {{ dateLabel(event.compare.trading_date) }}</span>
                <strong>{{ valueLabel(event.before) }}</strong>
              </div>
              <span class="discovery-arrow" aria-hidden="true">→</span>
              <div class="discovery-transition-state discovery-transition-state--after">
                <span>现在 · {{ dateLabel(event.current.trading_date) }}</span>
                <strong>{{ valueLabel(event.after) }}</strong>
              </div>
            </div>

            <div class="discovery-groups">
              <span>出现于</span>
              <span v-for="group in groupsLabel(event)" :key="group" class="discovery-group-badge">{{ group }}</span>
              <span v-if="!groupsLabel(event).length" class="discovery-group-badge">未标注观察组</span>
            </div>

            <p class="discovery-priority">
              <strong>为什么值得看</strong>
              {{ event.priority_reason }}
            </p>

            <div class="discovery-card-actions">
              <RouterLink :to="event.route" class="primary-button">看个股</RouterLink>
              <RouterLink
                :to="{ name: 'indicator-cross-section', params: { indicatorId: event.lens.id }, query: { run: currentRunId, discoveryEvent: event.event_id, symbol: event.symbol, lens: event.lens.id } }"
                class="secondary-button"
              >比较同类</RouterLink>
              <button
                v-if="focusStateKnown"
                type="button"
                class="secondary-button discovery-focus-button"
                data-testid="focus-toggle"
                :data-symbol="event.symbol"
                :disabled="pendingFocusSymbol === normalizeSymbol(event.symbol)"
                @click="toggleFocus(event.symbol)"
              >{{ pendingFocusSymbol === normalizeSymbol(event.symbol) ? '保存中…' : isFocused(event.symbol) ? '移出重点' : '加入重点' }}</button>
            </div>
          </article>
        </div>
            </section>
          </template>
        </section>
      </template>
    </template>
  </main>
</template>
