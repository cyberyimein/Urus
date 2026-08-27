<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import AppShell from '@/components/AppShell.vue'
import RemoteDecisionPanel from '@/components/decision/RemoteDecisionPanel.vue'
import { api } from '@/api/client'
import type { GroupDailySnapshot, ObservationGroup } from '@/types/api'
import type { RemoteDecisionSource } from '@/types/remoteDecision'

const route = useRoute()
const router = useRouter()
const groups = ref<ObservationGroup[]>([])
const detail = ref<{ group: ObservationGroup; latest_snapshot: GroupDailySnapshot | null } | null>(null)
const loading = ref(true)
const running = ref(false)
const error = ref('')
const runNotice = ref('')
const selectedSymbol = ref('')
const exactRunId = ref('')
const exactSnapshotId = ref('')
const groupRemotePanel = ref<{ open: () => void } | null>(null)

const selectedGroupId = computed(() => String(route.params.groupId ?? groups.value[0]?.group_id ?? ''))
const group = computed(() => detail.value?.group ?? groups.value.find((item) => item.group_id === selectedGroupId.value) ?? null)
const indicatorGroups = computed(() => groups.value.filter(isIndicatorGroup))
const themeGroups = computed(() => groups.value.filter((item) => !isIndicatorGroup(item)))
const snapshot = computed(() => detail.value?.latest_snapshot ?? null)
const groupAiSource = computed<RemoteDecisionSource>(() => ({
  observation_run_id: exactRunId.value || undefined,
  snapshot_id: exactSnapshotId.value || undefined,
  dataset_id: snapshot.value?.dataset_id,
  group_version_id: snapshot.value?.group?.version_id,
  content_sha256: snapshot.value?.content_sha256,
}))
const groupAiDisabled = computed(() => !snapshot.value || !exactRunId.value || !exactSnapshotId.value)
const features = computed(() => snapshot.value?.features ?? null)
const decision = computed(() => snapshot.value?.group_decision ?? null)
const relativeSeries = computed(() => snapshot.value?.charts.relative_strength.series ?? [])
const breadthSeries = computed(() => snapshot.value?.charts.breadth.series ?? {})
const rotation = computed(() => snapshot.value?.charts.rotation ?? [])
const heatmap = computed(() => snapshot.value?.charts.heatmap ?? [])
const smallMultiples = computed(() => snapshot.value?.charts.small_multiples ?? [])
const selectedSymbolRow = computed(() => snapshot.value?.symbols.find(item => item.symbol === selectedSymbol.value) ?? null)
const selectedStrategies = computed(() => (snapshot.value?.strategy_decisions ?? []).filter((item) => {
  const symbol = item.scope?.symbol ?? item.symbol
  return symbol === selectedSymbol.value
}))
const selectedMultiple = computed(() => smallMultiples.value.find(item => item.symbol === selectedSymbol.value) ?? null)

const actionLabels: Record<string, string> = {
  prioritize: '优先关注',
  watch: '等待确认',
  avoid: '暂时回避',
  no_action: '不行动',
}
const stateLabels: Record<string, string> = {
  broad_strength: '广泛走强',
  broad_weakness: '广泛走弱',
  narrow_leadership: '少数领涨',
  mixed: '内部混合',
  insufficient_data: '数据不足',
}

function isIndicatorGroup(value: ObservationGroup | null | undefined) {
  return value?.source === 'universe' && (value.tags ?? []).some((tag) => tag === 'indicator-recommendation' || tag === 'watchlist')
}

function isLegacySelfSelectedGroup(value: ObservationGroup) {
  return value.source === 'manual' && (value.tags ?? []).some((tag) => ['self-selected', 'user-selected', 'user-qualified'].includes(tag))
}

function normalizeGroup(value: ObservationGroup): ObservationGroup {
  if (!isIndicatorGroup(value)) return value
  return {
    ...value,
    display_name: '指标推荐',
    description: value.description === '由当前部署 Universe 的 equity_watchlist 自动同步。'
      ? '由当前部署 Universe 自动生成的指标推荐列表；不能手工编辑。'
      : value.description,
  }
}

function normalizeActiveGroups(values: ObservationGroup[]) {
  return values.map(normalizeGroup).filter((item) => !isLegacySelfSelectedGroup(item))
}

function formatPercent(value: number | null | undefined) {
  return value === null || value === undefined || !Number.isFinite(value) ? '—' : `${value >= 0 ? '+' : ''}${value.toFixed(1)}%`
}

function formatRatio(value: number | null | undefined) {
  return value === null || value === undefined || !Number.isFinite(value) ? '—' : `${(value * 100).toFixed(0)}%`
}

function linePath(points: Array<{ time: string; value: number | null; benchmark_value?: number | null }>, key: 'value' | 'benchmark_value' = 'value', minOverride?: number, maxOverride?: number) {
  const values = points.map((point) => point[key]).filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
  if (values.length < 2) return ''
  const min = minOverride ?? Math.min(...values)
  const max = maxOverride ?? Math.max(...values)
  const span = max - min || 1
  return points.map((point, index) => {
    const value = point[key]
    if (typeof value !== 'number') return ''
    const x = 16 + (index / Math.max(1, points.length - 1)) * 608
    const y = 198 - ((value - min) / span) * 174
    return `${index === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`
  }).filter(Boolean).join(' ')
}

function breadthPath(key: string) {
  return linePath(breadthSeries.value[key] ?? [], 'value', 0, 1)
}

function miniPath(points: Array<{ time: string; value: number | null; ma20: number | null; ma50: number | null }>, key: 'value' | 'ma20' | 'ma50') {
  const values = points.map((point) => point[key]).filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
  if (values.length < 2) return ''
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  return points.map((point, index) => {
    const value = point[key]
    if (typeof value !== 'number') return ''
    const x = 4 + (index / Math.max(1, points.length - 1)) * 172
    const y = 58 - ((value - min) / span) * 50
    return `${index === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`
  }).filter(Boolean).join(' ')
}

function rotationX(value: number | null) {
  const min = -20
  const max = 20
  return 20 + (((value ?? 0) - min) / (max - min)) * 560
}

function rotationY(value: number | null) {
  const min = -20
  const max = 20
  return 198 - (((value ?? 0) - min) / (max - min)) * 174
}

function heatTone(value: unknown) {
  if (value === 'strong' || value === 'leading' || value === 'volume_up_demand') return 'positive'
  if (value === 'weak' || value === 'lagging' || value === 'volume_down_distribution') return 'negative'
  if (value === 'oversold') return 'warning'
  return 'neutral'
}

function selectSymbol(symbol: string) {
  selectedSymbol.value = symbol
}

async function loadGroup(groupId: string) {
  if (!groupId) return
  try {
    const response = await api.getObservationGroup(groupId)
    detail.value = { ...response, group: normalizeGroup(response.group) }
    exactRunId.value = ''
    exactSnapshotId.value = ''
    const requestedRun = typeof route.query.run === 'string' ? route.query.run : ''
    if (requestedRun) {
      try {
        const exact = await api.getObservationRunGroupSnapshot(requestedRun, groupId)
        detail.value.latest_snapshot = exact.snapshot
        exactRunId.value = exact.observation_run_id
        exactSnapshotId.value = exact.snapshot_id
      } catch {
        // A Run may not contain every group visible in the current Universe.
        // Keep deterministic latest data visible, but do not offer AI on it.
      }
    }
    const available = detail.value.latest_snapshot?.symbols.map(item => String(item.symbol)) ?? []
    if (!available.includes(selectedSymbol.value)) {
      selectedSymbol.value = available[0] ?? ''
    }
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '组观察数据加载失败。'
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    groups.value = normalizeActiveGroups(await api.listObservationGroups())
    const requestedGroupId = String(route.params.groupId ?? '')
    const groupId = groups.value.some((item) => item.group_id === requestedGroupId)
      ? requestedGroupId
      : groups.value[0]?.group_id ?? ''
    if (groupId && groupId !== route.params.groupId) {
      await router.replace({ name: 'group-observation', params: { groupId } })
    }
    await loadGroup(groupId)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '观察组列表加载失败。'
  } finally {
    loading.value = false
  }
}

async function chooseGroup(groupId: string) {
  runNotice.value = ''
  await router.push({ name: 'group-observation', params: { groupId }, query: {} })
  await loadGroup(groupId)
}

async function runObservation() {
  if (!group.value || running.value) return
  running.value = true
  error.value = ''
  runNotice.value = ''
  try {
    const result = await api.createObservationRun({
      group_ids: [group.value.group_id],
      trigger_mode: 'manual',
      request_intent_id: `group-page:${group.value.group_id}:${Date.now()}`,
    })
    runNotice.value = `Observation Run ${result.status} · ${result.run_id.slice(0, 8)}…`
    await router.replace({ name: 'group-observation', params: { groupId: group.value.group_id }, query: { run: result.run_id } })
    await loadGroup(group.value.group_id)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : 'Observation Run 执行失败。'
  } finally {
    running.value = false
  }
}

onMounted(() => { void load() })
</script>

<template>
  <AppShell />
  <main class="phase-c-page">
    <section class="phase-c-hero">
      <div>
        <p class="eyebrow">OBSERVATION RUN / GROUP RESEARCH</p>
        <h1>组观察工作台</h1>
        <p>先看组的参与度，再看相对强弱和轮动，最后定位到具体个股。所有结果来自同一份收市冻结证据。</p>
      </div>
      <div class="phase-c-hero-actions">
        <span class="phase-c-lock">DETERMINISTIC ONLY</span>
        <RouterLink class="secondary-button" to="/observation-runs">运行总览</RouterLink>
        <button class="primary-button" type="button" :disabled="!group || running" @click="runObservation">{{ running ? '正在冻结…' : '执行收市观察' }}</button>
      </div>
    </section>

    <div v-if="error" class="error-banner" role="alert">{{ error }}</div>
    <div v-if="runNotice" class="phase-c-notice">{{ runNotice }}</div>

    <div class="phase-c-group-selector" aria-label="观察组">
      <section v-if="indicatorGroups.length" class="phase-c-group-tab-section phase-c-indicator-section">
        <div class="phase-c-group-section-label"><span>指标推荐</span><small>自动生成 · 只读</small></div>
        <div class="phase-c-group-tabs">
          <button v-for="item in indicatorGroups" :key="item.group_id" class="phase-c-group-tab" type="button" :class="{ active: item.group_id === selectedGroupId }" @click="chooseGroup(item.group_id)">
            <strong>{{ item.display_name }}</strong>
            <small>{{ item.symbols.length }} symbols · {{ item.benchmark_symbols.join(' / ') || 'no benchmark' }}</small>
          </button>
        </div>
      </section>
      <section v-if="themeGroups.length" class="phase-c-group-tab-section phase-c-theme-section">
        <div class="phase-c-group-section-label"><span>SECTOR WATCHLIST</span><small>Universe 主题组</small></div>
        <div class="phase-c-group-tabs">
          <button v-for="item in themeGroups" :key="item.group_id" class="phase-c-group-tab" type="button" :class="{ active: item.group_id === selectedGroupId }" @click="chooseGroup(item.group_id)">
            <strong>{{ item.display_name }}</strong>
            <small>{{ item.symbols.length }} symbols · {{ item.benchmark_symbols.join(' / ') || 'no benchmark' }}</small>
          </button>
        </div>
      </section>
    </div>

    <section v-if="loading" class="empty-panel"><p>正在读取观察组…</p></section>
    <section v-else-if="!group" class="empty-panel">
      <p class="eyebrow">NO OBSERVATION GROUP</p>
      <h2>还没有可用观察组。</h2>
      <p>先在 Universe 中维护指标推荐成员和主题归属，再同步生成观察组。</p>
      <RouterLink class="secondary-button" to="/settings/universe">打开 Universe 设置</RouterLink>
    </section>

    <template v-else>
      <section class="phase-c-group-head">
        <div>
          <div class="phase-c-breadcrumb">{{ isIndicatorGroup(group) ? '指标推荐' : 'SECTOR WATCHLIST' }} <span>/</span> {{ group.group_id }} <span>/</span> v{{ group.version }}</div>
          <h2>{{ group.display_name }}</h2>
          <p>{{ group.description || '用户维护的观察组。' }}<span v-if="group.source === 'universe' && (group.tags.includes('indicator-recommendation') || group.tags.includes('watchlist'))" class="phase-c-source-note">指标推荐由已部署 Universe 自动生成；不能在设置页或组观察页手动修改。</span></p>
          <small v-if="snapshot?.changes.previous_trading_date" class="phase-c-change-note">较 {{ snapshot.changes.previous_trading_date }}：组状态 {{ snapshot.changes.group_state.changed ? '发生变化' : '未变化' }} · MA20 广度 {{ formatPercent((snapshot.changes.breadth_ma20_delta ?? 0) * 100) }}</small>
        </div>
        <div class="phase-c-group-meta">
          <span>AS OF <strong>{{ snapshot?.trading_date ?? '尚未运行' }}</strong></span>
          <span>VALID <strong>{{ snapshot?.quality.valid_symbol_count ?? 0 }} / {{ group.symbols.length }}</strong></span>
          <span>BENCHMARK <strong>{{ group.benchmark_symbols.join(' / ') || '—' }}</strong></span>
        </div>
      </section>

      <section v-if="snapshot" class="phase-c-dashboard">
        <div class="phase-c-stat-strip">
          <div><span>GROUP STATE</span><strong :data-tone="decision?.stance">{{ stateLabels[decision?.state ?? ''] ?? decision?.state ?? '—' }}</strong><small>{{ actionLabels[decision?.action ?? ''] ?? decision?.action }}</small></div>
          <div><span>MEDIAN 20D</span><strong>{{ formatPercent(features?.returns_percent?.['20d']?.median) }}</strong><small>Q1 {{ formatPercent(features?.returns_percent?.['20d']?.q1) }} · Q3 {{ formatPercent(features?.returns_percent?.['20d']?.q3) }}</small></div>
          <div><span>ABOVE MA20</span><strong>{{ formatRatio(features?.breadth?.above_ma20) }}</strong><small>MA50 {{ formatRatio(features?.breadth?.above_ma50) }} · MA200 {{ formatRatio(features?.breadth?.above_ma200) }}</small></div>
          <div><span>RELATIVE 20D</span><strong>{{ formatPercent(Number(features?.relative_strength?.median_excess_20d ?? NaN)) }}</strong><small>{{ features?.relative_strength?.benchmark ?? '—' }}</small></div>
          <div><span>DISPERSION</span><strong>{{ formatPercent(features?.cross_sectional_dispersion_1d) }}</strong><small>leader concentration {{ formatRatio(features?.leader_concentration) }}</small></div>
        </div>

        <div class="phase-c-chart-grid">
          <article class="phase-c-card">
            <div class="phase-c-card-head"><div><span class="section-kicker">A · RELATIVE STRENGTH</span><h3>组相对强弱</h3></div><small>median normalized · {{ snapshot.charts.relative_strength.benchmark }}</small></div>
            <svg class="phase-c-chart" viewBox="0 0 640 230" role="img" aria-label="组相对强弱时间序列">
              <g class="phase-c-grid"><line x1="16" x2="624" y1="24" y2="24" /><line x1="16" x2="624" y1="111" y2="111" /><line x1="16" x2="624" y1="198" y2="198" /></g>
              <path :d="linePath(relativeSeries, 'value')" class="phase-c-line group-line" />
              <path :d="linePath(relativeSeries, 'benchmark_value')" class="phase-c-line benchmark-line" />
              <text x="18" y="18">GROUP</text><text x="82" y="18">{{ snapshot.charts.relative_strength.benchmark }}</text><text x="575" y="218">{{ snapshot.trading_date }}</text>
            </svg>
          </article>

          <article class="phase-c-card">
            <div class="phase-c-card-head"><div><span class="section-kicker">B · MARKET BREADTH</span><h3>市场广度</h3></div><small>有效样本 {{ snapshot.quality.valid_symbol_count }}</small></div>
            <svg class="phase-c-chart" viewBox="0 0 640 230" role="img" aria-label="组内均线广度">
              <g class="phase-c-grid"><line x1="16" x2="624" y1="24" y2="24" /><line x1="16" x2="624" y1="111" y2="111" /><line x1="16" x2="624" y1="198" y2="198" /></g>
              <path :d="breadthPath('above_ma20')" class="phase-c-line breadth-20" /><path :d="breadthPath('above_ma50')" class="phase-c-line breadth-50" /><path :d="breadthPath('above_ma200')" class="phase-c-line breadth-200" />
              <text x="18" y="18">100%</text><text x="18" y="218">0%</text><text x="555" y="218">{{ snapshot.trading_date }}</text>
            </svg>
            <div class="phase-c-legend"><span><i class="breadth-20"></i> MA20</span><span><i class="breadth-50"></i> MA50</span><span><i class="breadth-200"></i> MA200</span></div>
          </article>

          <article class="phase-c-card">
            <div class="phase-c-card-head"><div><span class="section-kicker">C · ROTATION MAP</span><h3>轮动象限</h3></div><small>X 相对强弱 · Y 5D 变化</small></div>
            <svg class="phase-c-chart phase-c-rotation" viewBox="0 0 600 230" role="img" aria-label="组内轮动象限">
              <rect x="20" y="24" width="280" height="174" class="rotation-quadrant weak-improving" /><rect x="300" y="24" width="280" height="174" class="rotation-quadrant leading-improving" />
              <line x1="300" x2="300" y1="24" y2="198" class="rotation-axis" /><line x1="20" x2="580" y1="111" y2="111" class="rotation-axis" />
              <text x="28" y="42">改善但落后</text><text x="438" y="42">领先并增强</text><text x="28" y="188">落后且恶化</text><text x="438" y="188">领先但减弱</text>
              <g v-for="point in rotation" :key="point.symbol" class="phase-c-selectable-point" :class="{ active: point.symbol === selectedSymbol }" role="button" tabindex="0" @click="selectSymbol(point.symbol)" @keydown.enter="selectSymbol(point.symbol)"><circle :cx="rotationX(point.x_relative_20d)" :cy="rotationY(point.y_relative_change)" :r="Math.max(4, Math.min(10, (point.size ?? 1) * 3))" :data-tone="point.stance" /><text :x="rotationX(point.x_relative_20d) + 7" :y="rotationY(point.y_relative_change) + 3">{{ point.symbol }}</text></g>
            </svg>
          </article>

          <article class="phase-c-card phase-c-heatmap-card">
            <div class="phase-c-card-head"><div><span class="section-kicker">D · SYMBOL STATE</span><h3>个股状态热力图</h3></div><small>按 20D 变化排序</small></div>
            <div class="phase-c-heatmap"><div class="heatmap-row heatmap-header"><span>SYMBOL</span><span>TREND</span><span>MOMENTUM</span><span>VOLUME</span><span>RELATIVE</span><span>20D</span></div><button v-for="row in heatmap" :key="row.symbol" class="heatmap-row" :class="{ active: row.symbol === selectedSymbol }" type="button" @click="selectSymbol(row.symbol)"><strong>{{ row.symbol }}</strong><span :data-tone="heatTone(row.trend)">{{ row.trend }}</span><span :data-tone="heatTone(row.momentum)">{{ row.momentum }}</span><span :data-tone="heatTone(row.volume)">{{ row.volume }}</span><span :data-tone="heatTone(row.relative)">{{ row.relative }}</span><b :data-tone="(row.return_20d ?? 0) >= 0 ? 'positive' : 'negative'">{{ formatPercent(row.return_20d) }}</b></button></div>
          </article>
        </div>

        <section v-if="selectedSymbolRow" class="phase-c-card phase-c-symbol-focus">
          <div class="phase-c-card-head"><div><span class="section-kicker">SELECTED SYMBOL / FROZEN EVIDENCE</span><h3>{{ selectedSymbol }}</h3></div><RouterLink class="text-link" :to="{ name: 'instrument-decision', params: { symbol: selectedSymbol }, query: { dataset: snapshot.dataset_id } }">打开完整个股 →</RouterLink></div>
          <div class="phase-c-focus-grid">
            <div class="phase-c-focus-chart"><svg v-if="selectedMultiple" viewBox="0 0 360 120" role="img" :aria-label="`${selectedSymbol} 冻结形态`"><path :d="miniPath(selectedMultiple.points, 'value')" class="small-close-line" /><path :d="miniPath(selectedMultiple.points, 'ma20')" class="small-ma20-line" /><path :d="miniPath(selectedMultiple.points, 'ma50')" class="small-ma50-line" /></svg></div>
            <dl><div><dt>CLOSE</dt><dd>{{ selectedSymbolRow.latest_close ?? '—' }}</dd></div><div><dt>20D</dt><dd>{{ formatPercent(selectedSymbolRow.returns_percent?.['20']) }}</dd></div><div><dt>RSI</dt><dd>{{ typeof selectedSymbolRow.rsi14 === 'number' ? `RSI ${selectedSymbolRow.rsi14.toFixed(1)}` : '—' }}</dd></div><div><dt>RELATIVE</dt><dd>{{ formatPercent(selectedSymbolRow.relative_excess_percent?.['20d']) }}</dd></div><div><dt>QUALITY</dt><dd>{{ selectedSymbolRow.quality_status }}</dd></div></dl>
            <div class="phase-c-focus-strategies"><p v-if="!selectedStrategies.length">没有可用策略判断。</p><article v-for="item in selectedStrategies" :key="item.decision_id"><strong>{{ item.strategy?.name }}</strong><span>{{ item.stance }} · {{ item.action }} · {{ item.setup_progress?.stage }}</span><small>{{ item.confirmation_conditions?.[0] ?? item.reasons?.[0] ?? '等待更多证据。' }}</small></article></div>
          </div>
        </section>

        <section class="phase-c-lower-grid">
          <article class="phase-c-card phase-c-ranking-card"><div class="phase-c-card-head"><div><span class="section-kicker">LEADERS / LAGGARDS</span><h3>组内排名</h3></div><small>20D return</small></div><div class="phase-c-rank-columns"><div><span class="rank-label positive">LEADERS</span><button v-for="row in features?.leaders" :key="`leader-${row.symbol}`" type="button" @click="selectSymbol(row.symbol)"><strong>{{ row.symbol }}</strong><b>{{ formatPercent(row.return_percent) }}</b></button></div><div><span class="rank-label negative">LAGGARDS</span><button v-for="row in features?.laggards" :key="`laggard-${row.symbol}`" type="button" @click="selectSymbol(row.symbol)"><strong>{{ row.symbol }}</strong><b>{{ formatPercent(row.return_percent) }}</b></button></div></div></article>
          <article class="phase-c-card phase-c-decision-card"><div class="phase-c-card-head"><div><span class="section-kicker">GROUP DECISION</span><h3>确定性组建议</h3></div><span class="phase-c-decision-pill" :data-tone="decision?.stance">{{ actionLabels[decision?.action ?? ''] ?? decision?.action }}</span></div><p>{{ decision?.reasons?.[0] ?? '等待足够的组级证据。' }}</p><small v-if="!exactRunId">请先从某次 Observation Run 打开本组的精确快照，才能发起 AI 仲裁。</small><RemoteDecisionPanel ref="groupRemotePanel" intent-type="group_arbitration" :source="groupAiSource" title="确认组级 AI 仲裁" label="主动发起组级 AI 仲裁" :disabled="groupAiDisabled" preflight-on-mount /></article>
        </section>

        <section class="phase-c-card phase-c-small-multiples">
          <div class="phase-c-card-head"><div><span class="section-kicker">SMALL MULTIPLES</span><h3>个股形态速览</h3></div><small>60D normalized · 点击进入个股</small></div>
          <div class="phase-c-small-grid">
            <button v-for="item in smallMultiples" :key="item.symbol" class="phase-c-small-card" :class="{ active: item.symbol === selectedSymbol }" type="button" @click="selectSymbol(item.symbol)">
              <div><strong>{{ item.symbol }}</strong><b :data-tone="(item.return_20d ?? 0) >= 0 ? 'positive' : 'negative'">{{ formatPercent(item.return_20d) }}</b></div>
              <svg viewBox="0 0 180 64" role="img" :aria-label="`${item.symbol} 60 日形态`"><path :d="miniPath(item.points, 'value')" class="small-close-line" /><path :d="miniPath(item.points, 'ma20')" class="small-ma20-line" /><path :d="miniPath(item.points, 'ma50')" class="small-ma50-line" /></svg>
            </button>
          </div>
        </section>
      </section>
      <section v-else class="empty-panel phase-c-empty"><p class="eyebrow">NO FROZEN OBSERVATION</p><h2>还没有组级收市观察。</h2><p>点击右上角“执行收市观察”，系统会冻结组内日 K 并计算广度、轮动和热力图。</p><button class="primary-button" type="button" :disabled="running" @click="runObservation">执行第一次观察</button></section>
    </template>
  </main>
</template>
