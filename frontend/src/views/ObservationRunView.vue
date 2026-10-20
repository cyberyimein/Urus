<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import AppShell from '@/components/AppShell.vue'
import { api } from '@/api/client'
import type { ObservationRun, ObservationRunSummary } from '@/types/api'

const runs = ref<ObservationRunSummary[]>([])
const selectedRunId = ref('')
const selectedRun = ref<ObservationRun | null>(null)
const detailLoading = ref(false)
let detailGeneration = 0
let listGeneration = 0
let detailController: AbortController | null = null
const loading = ref(true)
const running = ref(false)
const syncing = ref(false)
const error = ref('')
const notice = ref('')

const selectedSummary = computed(() => runs.value.find(item => item.run_id === selectedRunId.value) ?? null)
const report = computed(() => selectedRun.value?.report ?? null)

const stateLabels: Record<string, string> = {
  broad_strength: '广泛走强', broad_weakness: '广泛走弱', narrow_leadership: '少数领涨',
  mixed: '内部混合', insufficient_data: '数据不足', confirmed: '已确认',
  invalidated: '已失效', armed: '接近确认', near_confirmation: '接近确认',
  watching: '形成中', forming: '形成中', bearish: '弱势 / 多头回避',
}
const impulseLabels: Record<string, string> = {
  improving: '改善', deteriorating: '恶化', stable: '稳定', unavailable: '暂无比较',
}

function formatPercent(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? `${value >= 0 ? '+' : ''}${value.toFixed(1)}%` : '—'
}

function formatRatio(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(0)}%` : '—'
}

function shortHash(value: unknown) {
  return typeof value === 'string' && value ? `${value.slice(0, 10)}…` : '—'
}

function instrumentLink(item: Record<string, any>) {
  return {
    name: 'instrument-decision',
    params: { symbol: item.symbol },
    query: {
      ...(item.dataset_id ? { dataset: item.dataset_id } : {}),
      ...(selectedRun.value?.run_id ? { run: selectedRun.value.run_id } : {}),
    },
  }
}

async function loadSelectedRun() {
  const generation = ++detailGeneration
  detailController?.abort()
  selectedRun.value = null
  error.value = ''
  const runId = selectedRunId.value
  if (!runId) {
    detailLoading.value = false
    return
  }
  const controller = new AbortController()
  detailController = controller
  detailLoading.value = true
  try {
    const detail = await api.getObservationRun(runId, controller.signal)
    if (generation !== detailGeneration || controller.signal.aborted) return
    if (detail.run_id !== runId) throw new Error('运行详情身份不一致，已拒绝加载。')
    selectedRun.value = detail
  } catch (reason) {
    if (generation === detailGeneration && !controller.signal.aborted) {
      error.value = reason instanceof Error ? reason.message : 'Observation Run 详情加载失败。'
    }
  } finally {
    if (generation === detailGeneration) detailLoading.value = false
  }
}

async function load(preferredRunId = selectedRunId.value) {
  const generation = ++listGeneration
  loading.value = true
  error.value = ''
  try {
    const summaries = await api.listObservationRunSummaries()
    if (generation !== listGeneration) return
    runs.value = summaries
    selectedRunId.value = runs.value.some(item => item.run_id === preferredRunId)
      ? preferredRunId
      : runs.value.find(item => item.status === 'succeeded' || item.status === 'mixed')?.run_id ?? runs.value[0]?.run_id ?? ''
    await loadSelectedRun()
  } catch (reason) {
    if (generation === listGeneration) {
      error.value = reason instanceof Error ? reason.message : 'Observation Run 列表加载失败。'
    }
  } finally {
    if (generation === listGeneration) loading.value = false
  }
}

async function syncGroups() {
  if (syncing.value) return
  syncing.value = true
  error.value = ''
  try {
    const result = await api.syncObservationGroups()
    notice.value = `关注列表已同步 · ${result.symbol_count} symbols · ${result.group_count} groups · ${result.universe_freshness}`
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '关注列表同步失败。'
  } finally {
    syncing.value = false
  }
}

async function runAllGroups() {
  if (running.value) return
  running.value = true
  error.value = ''
  notice.value = ''
  try {
    await api.syncObservationGroups()
    const result = await api.createObservationRun({
      trigger_mode: 'manual',
      request_intent_id: `observation-overview:${Date.now()}`,
    })
    notice.value = `Observation Run ${result.status} · ${result.run_id.slice(0, 8)}…`
    await load(result.run_id)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : 'Observation Run 执行失败。'
  } finally {
    running.value = false
  }
}

onMounted(() => { void load() })
onBeforeUnmount(() => {
  listGeneration += 1
  detailGeneration += 1
  detailController?.abort()
})
</script>

<template>
  <AppShell />
  <main class="observation-page observation-run-page">
    <section class="observation-hero">
      <div>
        <p class="eyebrow">POST-CLOSE OBSERVATION / RUN</p>
        <h1>收市观察总览</h1>
        <p>冻结实际关注列表、日 K、指标和确定性策略；所有结论均可回到同一 Observation Run 的证据。</p>
      </div>
      <div class="observation-hero-actions">
        <span class="observation-lock">DETERMINISTIC ONLY</span>
        <button class="secondary-button" type="button" :disabled="syncing" @click="syncGroups">{{ syncing ? '同步中…' : '同步关注列表' }}</button>
        <button class="primary-button" type="button" :disabled="running" @click="runAllGroups">{{ running ? '正在执行…' : '执行全部观察组' }}</button>
      </div>
    </section>

    <div v-if="error" class="error-banner" role="alert">{{ error }}</div>
    <div v-if="notice" class="observation-notice">{{ notice }}</div>
    <section v-if="loading" class="empty-panel"><p>正在读取收市观察…</p></section>
    <section v-else-if="!selectedSummary" class="empty-panel observation-empty">
      <p class="eyebrow">NO OBSERVATION RUN</p><h2>还没有收市观察。</h2>
      <p>同步实际关注列表并执行一次观察后，这里会显示完整确定性报告。</p>
      <button class="primary-button" type="button" :disabled="running" @click="runAllGroups">执行第一次观察</button>
    </section>

    <template v-else>
      <section class="observation-run-toolbar">
        <label><span>OBSERVATION RUN</span><select v-model="selectedRunId" @change="loadSelectedRun"><option v-for="run in runs" :key="run.run_id" :value="run.run_id">{{ run.trading_date }} · {{ run.status }} · {{ run.trigger_mode }}</option></select></label>
        <div><span>REPORT HASH</span><strong>{{ shortHash(report?.content_sha256) }}</strong></div>
      </section>

      <section v-if="detailLoading" class="empty-panel"><p>正在读取运行报告…</p></section>
      <section v-if="selectedRun" class="observation-stat-strip observation-run-stat-strip">
        <div><span>RUN STATUS</span><strong :data-tone="selectedRun.status === 'succeeded' ? 'bullish' : selectedRun.status === 'failed' ? 'bearish' : 'neutral'">{{ selectedRun.status }}</strong><small>{{ selectedRun.run_id.slice(0, 12) }}…</small></div>
        <div><span>TRADING DATE</span><strong>{{ selectedRun.trading_date }}</strong><small>{{ selectedRun.trigger_mode }}</small></div>
        <div><span>GROUPS</span><strong>{{ selectedRun.successful_group_count ?? selectedRun.group_count }} / {{ selectedRun.group_count }}</strong><small>successful</small></div>
        <div><span>EXCEPTIONS</span><strong>{{ report?.summary.quality_issue_count ?? selectedRun.failed_group_count ?? 0 }}</strong><small>quality / group failures</small></div>
        <div><span>UNIVERSE</span><strong>{{ selectedRun.universe_freshness }}</strong><small>{{ selectedRun.universe_revision_id ? shortHash(selectedRun.universe_revision_id) : 'not attached' }}</small></div>
      </section>

      <template v-if="report && selectedRun">
        <section class="observation-card observation-provenance-card">
          <div class="observation-card-head"><div><span class="section-kicker">UNIVERSE PROVENANCE</span><h3>本次运行的关注列表来源</h3></div><small>{{ report.provenance?.universe_freshness ?? selectedRun.universe_freshness }}</small></div>
          <p class="observation-muted-row">{{ report.provenance?.universe_source_url ?? selectedRun.universe_source_url ?? '本地 Universe' }} · Revision {{ report.provenance?.universe_revision_id ?? selectedRun.universe_revision_id ?? '—' }}</p>
        </section>
        <section class="observation-run-summary observation-theme-summary">
          <div class="observation-section-heading"><div><span class="section-kicker">DECISION PHASES</span><h2>领先 / 恶化 / 潜在反转主题</h2></div><small>强弱 × 边际变化 × setup</small></div>
          <div class="observation-three-columns">
            <article class="observation-card"><h3 class="positive">领先主题</h3><p v-if="!report.leading_themes?.length" class="observation-muted-row">暂无满足 setup 的领先主题。</p><RouterLink v-for="item in report.leading_themes ?? []" :key="item.group_id" class="observation-list-row" :to="`/groups/${item.group_id}`"><strong>{{ item.group_name }}</strong><span>{{ item.phase }}</span></RouterLink></article>
            <article class="observation-card"><h3 class="negative">恶化主题</h3><p v-if="!report.deteriorating_themes?.length" class="observation-muted-row">暂无强势恶化主题。</p><RouterLink v-for="item in report.deteriorating_themes ?? []" :key="item.group_id" class="observation-list-row" :to="`/groups/${item.group_id}`"><strong>{{ item.group_name }}</strong><span>{{ item.phase }}</span></RouterLink></article>
            <article class="observation-card"><h3>潜在反转主题</h3><p v-if="!report.reversal_themes?.length" class="observation-muted-row">暂无带 setup 的改善弱势组。</p><RouterLink v-for="item in report.reversal_themes ?? []" :key="item.group_id" class="observation-list-row" :to="`/groups/${item.group_id}`"><strong>{{ item.group_name }}</strong><span>{{ item.phase }}</span></RouterLink></article>
          </div>
        </section>
        <section class="observation-run-visuals">
          <article class="observation-card observation-momentum-map">
            <div class="observation-card-head"><div><span class="section-kicker">GROUP MOMENTUM MAP</span><h3>组动量与变化</h3></div><small>相对强弱 × 日变化</small></div>
            <div class="observation-momentum-grid">
              <RouterLink v-for="item in report.visuals.group_momentum_map" :key="item.group_id" :to="`/groups/${item.group_id}`" :data-tone="(item.relative_20d_change ?? 0) >= 0 ? 'positive' : 'negative'">
                <strong>{{ item.group_name }}</strong><span>{{ formatPercent(item.relative_20d) }}</span><small>Δ {{ formatPercent(item.relative_20d_change) }} · breadth {{ formatRatio(item.breadth_ma20) }}</small>
              </RouterLink>
            </div>
          </article>
          <article class="observation-card">
            <div class="observation-card-head"><div><span class="section-kicker">STATE TRANSITIONS</span><h3>组状态转换</h3></div><small>{{ report.visuals.state_transitions.length }} changes</small></div>
            <p v-if="!report.visuals.state_transitions.length" class="observation-muted-row">本次没有已确认的组状态转换。</p>
            <p v-for="item in report.visuals.state_transitions" :key="item.group_id" class="observation-list-row"><strong>{{ item.group_name }}</strong><span>{{ stateLabels[item.from] ?? item.from }} → {{ stateLabels[item.to] ?? item.to }}</span></p>
          </article>
        </section>

        <section class="observation-run-summary">
          <div class="observation-section-heading"><div><span class="section-kicker">TODAY'S CHANGE</span><h2>改善 / 恶化组</h2></div><small>同一交易日 · 同一冻结运行</small></div>
          <div class="observation-two-columns">
            <article class="observation-card"><h3 class="positive">改善</h3><p v-if="!report.improving_groups.length" class="observation-muted-row">无可比较的改善组。</p><RouterLink v-for="item in report.improving_groups" :key="item.group_id" class="observation-list-row" :to="`/groups/${item.group_id}`"><strong>{{ item.group_name }}</strong><span>{{ formatPercent(item.change_score) }}</span></RouterLink></article>
            <article class="observation-card"><h3 class="negative">恶化</h3><p v-if="!report.deteriorating_groups.length" class="observation-muted-row">无可比较的恶化组。</p><RouterLink v-for="item in report.deteriorating_groups" :key="item.group_id" class="observation-list-row" :to="`/groups/${item.group_id}`"><strong>{{ item.group_name }}</strong><span>{{ formatPercent(item.change_score) }}</span></RouterLink></article>
          </div>
        </section>

        <section class="observation-card observation-ranking-table">
          <div class="observation-card-head"><div><span class="section-kicker">GROUP RANKING</span><h3>组强弱排行</h3></div><small>透明技术分；不是概率</small></div>
          <p v-if="report.ranking_status === 'unavailable' || report.group_rankings.every(item => item.ranking_status === 'unavailable')" class="observation-muted-row">样本不足，截面排名不可用；仅展示组决策阶段。</p>
          <RouterLink v-for="(item, index) in report.group_rankings" :key="item.group_id" class="observation-ranking-row" :to="`/groups/${item.group_id}`"><b>{{ index + 1 }}</b><strong>{{ item.group_name }}</strong><span>{{ stateLabels[item.state] ?? item.state }} · {{ impulseLabels[item.impulse] ?? item.impulse ?? '—' }}</span><span>20D {{ formatPercent(item.median_20d) }}</span><span>RS {{ formatPercent(item.relative_20d) }}</span><span>MA20 {{ formatRatio(item.breadth_ma20) }}</span></RouterLink>
        </section>

        <section class="observation-two-columns observation-anomalies">
          <article class="observation-card"><div class="observation-card-head"><div><span class="section-kicker">POSITIVE OUTLIERS</span><h3>异常强个股</h3></div></div><RouterLink v-for="item in report.anomalies.leaders" :key="`${item.group_id}:${item.symbol}`" class="observation-list-row" :to="instrumentLink(item)"><span><strong>{{ item.symbol }}</strong><small>{{ item.group_name }}</small></span><span>{{ formatPercent(item.return_20d) }} / RS {{ formatPercent(item.relative_20d) }}</span></RouterLink></article>
          <article class="observation-card"><div class="observation-card-head"><div><span class="section-kicker">NEGATIVE OUTLIERS</span><h3>异常弱个股</h3></div></div><RouterLink v-for="item in report.anomalies.laggards" :key="`${item.group_id}:${item.symbol}`" class="observation-list-row" :to="instrumentLink(item)"><span><strong>{{ item.symbol }}</strong><small>{{ item.group_name }}</small></span><span>{{ formatPercent(item.return_20d) }} / RS {{ formatPercent(item.relative_20d) }}</span></RouterLink></article>
        </section>

        <section class="observation-lanes">
          <article class="observation-card"><div class="observation-card-head"><div><span class="section-kicker">OPPORTUNITY LANES</span><h3>机会泳道</h3></div></div><div v-for="(items, lane) in report.opportunity_lanes" :key="lane" class="observation-lane"><strong>{{ stateLabels[String(lane)] ?? lane }}</strong><span v-if="!items.length">—</span><RouterLink v-for="item in items" :key="item.decision_id" :to="instrumentLink(item)">{{ item.symbol }} · {{ item.strategy_name }}<small v-if="item.setup_plan?.confirmation_price != null">触发 {{ item.setup_plan.confirmation_operator }} {{ item.setup_plan.confirmation_price }} · {{ item.setup_plan.remaining_atr ?? '—' }} ATR · 失效 {{ item.setup_plan.invalidation_price ?? '—' }}</small></RouterLink></div></article>
          <article class="observation-card"><div class="observation-card-head"><div><span class="section-kicker">RISK LANES</span><h3>风险泳道</h3></div></div><div v-for="(items, lane) in report.risk_lanes" :key="lane" class="observation-lane"><strong>{{ stateLabels[String(lane)] ?? lane }}</strong><span v-if="!items.length">—</span><RouterLink v-for="item in items" :key="item.decision_id" :to="instrumentLink(item)">{{ item.symbol }} · {{ item.strategy_name }}</RouterLink></div></article>
        </section>

        <section v-if="report.strategy_conflicts.length || report.quality_issues.length" class="observation-two-columns">
          <article class="observation-card"><div class="observation-card-head"><div><span class="section-kicker">STRATEGY CONFLICTS</span><h3>策略冲突</h3></div><small>{{ report.strategy_conflicts.length }}</small></div><RouterLink v-for="item in report.strategy_conflicts" :key="`${item.group_id}:${item.symbol}`" class="observation-list-row" :to="instrumentLink(item)"><strong>{{ item.symbol }} · {{ (item.group_names ?? [item.group_name]).join(' / ') }}</strong><span>{{ item.summary }}</span></RouterLink><p v-if="!report.strategy_conflicts.length" class="observation-muted-row">未发现方向冲突。</p></article>
          <article class="observation-card"><div class="observation-card-head"><div><span class="section-kicker">DATA QUALITY</span><h3>数据问题</h3></div><small>{{ report.quality_issues.length }}</small></div><p v-for="(item, index) in report.quality_issues" :key="index" class="observation-list-row"><strong>{{ item.symbol ?? item.group_id }}</strong><span>{{ item.status }} · {{ item.message }}</span></p></article>
        </section>
      </template>

      <section class="observation-card observation-run-history"><div class="observation-card-head"><div><span class="section-kicker">RUN HISTORY</span><h3>运行和证据详情</h3></div><small>交易日、组版本和触发模式均冻结</small></div><button v-for="run in runs" :key="run.run_id" class="observation-run-history-row" type="button" @click="selectedRunId = run.run_id"><strong>{{ run.trading_date }}</strong><span>{{ run.status }}</span><span>{{ run.group_count }} groups</span><small>{{ run.run_id }}</small></button></section>
    </template>
  </main>
</template>
