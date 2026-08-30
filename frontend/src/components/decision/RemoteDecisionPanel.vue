<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import RemoteDecisionConfirmDialog from '@/components/decision/RemoteDecisionConfirmDialog.vue'
import { useRemoteDecision } from '@/composables/useRemoteDecision'
import type { RemoteDecisionIntent, RemoteDecisionSource } from '@/types/remoteDecision'
import { remoteDecisionTitle } from '@/utils/remoteDecisionDisplay'

const props = withDefaults(defineProps<{
  intentType: RemoteDecisionIntent
  source: RemoteDecisionSource
  title?: string
  label?: string
  disabled?: boolean
  compact?: boolean
  preflightOnMount?: boolean
  triggerClass?: string
}>(), {
  title: 'AI 决策',
  label: 'AI 评估',
  disabled: false,
  compact: false,
  preflightOnMount: false,
  triggerClass: '',
})

const confirmVisible = ref(false)
const stopConfirmVisible = ref(false)
const route = useRoute()
const router = useRouter()
const { preflight, run, history, loading, error, prepare, submit, restore, stop, rerun, reset } = useRemoteDecision()
const blocker = computed(() => preflight.value?.blockers[0]?.message ?? '')
const returnTo = computed(() => {
  const query = { ...route.query }
  if (props.intentType === 'instrument_arbitration' && props.source.dataset_id) {
    // The normal instrument page creates a frozen dataset without putting its
    // id in the URL. Carry it through the detail link so returning does not
    // silently refreeze a different dataset and miss the restored Run.
    query.dataset = props.source.dataset_id
  }
  if (
    (props.intentType === 'group_arbitration' || props.intentType === 'indicator_attention' || props.intentType === 'strategy_attention')
    && props.source.observation_run_id
  ) {
    query.run = props.source.observation_run_id
  }
  return router.resolve({ path: route.path, query, hash: route.hash }).fullPath
})
const detailRoute = computed(() => ({
  name: 'remote-decision-run',
  params: { localRunId: run.value?.local_run_id },
  query: { return_to: returnTo.value },
}))
const historyRuns = computed(() => history.value.slice(0, 8))
// ``succeeded`` is an Urus-internal hand-off state while Artifact validation
// finishes; it is no longer stoppable even though polling must continue.
const isTerminal = computed(() => ['succeeded', 'accepted', 'rejected_result', 'failed', 'stopped'].includes(run.value?.status ?? ''))
const triggerDisabled = computed(() => props.disabled || (props.preflightOnMount && !preflight.value?.enabled))

async function open() {
  if (props.disabled) return
  const next = await prepare(props.intentType, props.source)
  if (next?.enabled) confirmVisible.value = true
}

async function confirm() {
  confirmVisible.value = false
  await submit(props.intentType, props.source, crypto.randomUUID())
}

function requestStop() {
  if (!run.value || isTerminal.value) return
  stopConfirmVisible.value = true
}

function historyRoute(item: { local_run_id: string }) {
  return {
    name: 'remote-decision-run',
    params: { localRunId: item.local_run_id },
    query: { return_to: returnTo.value },
  }
}

function historyDate(item: { created_at: string; result: Record<string, any> | null }) {
  const tradingDate = item.result?.decision?.scope?.trading_date
  return typeof tradingDate === 'string' && tradingDate ? tradingDate : item.created_at.slice(0, 10)
}

function historyStatus(status: string) {
  const labels: Record<string, string> = {
    accepted: '已验收',
    rejected_result: '结果拒绝',
    failed: '失败',
    stopped: '已停止',
    running: '运行中',
    queued: '排队中',
    submitting: '提交中',
    succeeded: '待验收',
  }
  return labels[status] ?? status
}

async function confirmStop() {
  stopConfirmVisible.value = false
  await stop()
}

async function rerunSameEvidence() {
  await rerun()
}

async function refreshPreflight() {
  if (props.disabled || !props.preflightOnMount) return
  try {
    await prepare(props.intentType, props.source)
  } catch {
    // A network failure should leave the action gated without making the
    // deterministic page itself look broken; the next click retries it.
  }
}

async function restoreRun() {
  if (props.disabled) return
  await restore(props.intentType, props.source)
}

watch(() => [props.disabled, props.intentType, props.source], () => {
  confirmVisible.value = false
  stopConfirmVisible.value = false
  reset()
  void refreshPreflight()
  void restoreRun()
}, { deep: true, immediate: true })

defineExpose({ open })
</script>

<template>
  <div class="remote-decision-panel" :class="{ compact }">
    <button class="remote-decision-trigger" :class="[triggerClass, { compact }]" type="button" :disabled="triggerDisabled || loading" @click="open">
      <span>{{ loading ? '准备中…' : label }}</span><small v-if="!compact">主动确认冻结证据 · Anomalo Workflow</small><b>→</b>
    </button>
    <div v-if="preflight && !preflight.enabled" class="remote-decision-blocker" role="status">
      <strong>当前不可用</strong><span>{{ blocker }}</span>
    </div>
    <div v-if="error" class="remote-decision-error" role="alert">{{ error }}</div>
    <article v-if="run" class="remote-decision-run-summary">
      <header><span class="section-kicker">REMOTE DECISION RUN</span><strong>{{ run.status }}</strong></header>
      <p>{{ run.safe_error_message ?? (run.result ? remoteDecisionTitle(run.result) : 'Workflow 正在读取冻结证据。') }}</p>
      <div class="remote-decision-run-meta"><span>{{ run.local_run_id.slice(0, 12) }}…</span><span>events {{ run.latest_event_sequence }}</span><RouterLink :to="detailRoute">详情 →</RouterLink></div>
      <button v-if="!isTerminal" class="remote-stop-button" type="button" @click="requestStop">停止运行</button>
      <button v-if="isTerminal" class="remote-rerun-button" type="button" :disabled="loading" @click="rerunSameEvidence">用同一证据重新运行</button>
      <div v-if="run.result?.notable_cards?.length" class="remote-notable-cards">
        <a v-for="card in run.result.notable_cards.slice(0, 5)" :key="String(card.card_id)" :href="`#card-${String(card.card_id)}`"><b>#{{ card.rank }}</b><strong>{{ card.symbol ?? card.card_id }}</strong><span>{{ card.why_notable ?? card.finding_type ?? '值得关注' }}</span></a>
      </div>
    </article>
    <article v-if="historyRuns.length" class="remote-decision-history">
      <header><span class="section-kicker">DECISION HISTORY</span><strong>最近 {{ history.length }} 次</strong></header>
      <RouterLink v-for="item in historyRuns" :key="item.local_run_id" class="remote-history-item" :to="historyRoute(item)">
        <div><strong>{{ historyDate(item) }}</strong><small>{{ item.workflow_ref }} · {{ historyStatus(item.status) }}</small></div>
        <span>{{ item.result ? remoteDecisionTitle(item.result) : item.safe_error_message ?? '暂无结果' }}</span>
        <b>→</b>
      </RouterLink>
    </article>
    <RemoteDecisionConfirmDialog v-model="confirmVisible" :preflight="preflight" :title="title" @confirm="confirm" />
    <Teleport to="body">
      <div v-if="stopConfirmVisible" class="remote-confirm-backdrop" role="presentation" @click.self="stopConfirmVisible = false">
        <section class="remote-confirm-dialog" role="dialog" aria-modal="true" aria-label="确认停止 AI 运行">
          <p class="section-kicker">STOP REMOTE WORKFLOW</p>
          <h2>停止这次 AI 运行？</h2>
          <p>将请求 Anomalo 停止当前 Run。已产生的确定性证据和历史结果不会被修改。</p>
          <div class="remote-confirm-actions">
            <button class="secondary-button" type="button" @click="stopConfirmVisible = false">取消</button>
            <button class="primary-button remote-confirm-stop" type="button" @click="confirmStop">确认停止</button>
          </div>
        </section>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.remote-decision-panel { display: grid; gap: 10px; min-width: 0; }
.remote-decision-trigger { display: flex; align-items: center; gap: 12px; width: 100%; padding: 13px 15px; border: 1px solid rgba(105, 212, 173, .55); border-radius: 11px; background: linear-gradient(110deg, rgba(46, 159, 126, .2), rgba(32, 67, 100, .18)); color: #e9fff8; cursor: pointer; text-align: left; }
.remote-decision-trigger:hover:not(:disabled) { border-color: #76e6c0; transform: translateY(-1px); }
.remote-decision-trigger:disabled { opacity: .58; cursor: not-allowed; }
.remote-decision-trigger span { font-weight: 700; }
.remote-decision-trigger small { flex: 1; color: #9cb9b5; font-size: 11px; }
.remote-decision-trigger b { margin-left: auto; font-size: 18px; }
.remote-decision-trigger.compact { width: auto; white-space: nowrap; }
.remote-decision-blocker, .remote-decision-error { display: grid; gap: 3px; padding: 10px 12px; border-radius: 9px; background: rgba(205, 132, 76, .1); color: #e5b68e; font-size: 12px; }
.remote-decision-error { background: rgba(221, 92, 92, .12); color: #ffc1c1; }
.remote-decision-run-summary { padding: 14px; border: 1px solid rgba(120, 150, 184, .24); border-radius: 11px; background: rgba(15, 31, 51, .7); }
.remote-decision-run-summary header, .remote-decision-run-meta { display: flex; align-items: center; gap: 10px; }
.remote-decision-run-summary header strong { margin-left: auto; color: #91e4bf; font-size: 12px; text-transform: uppercase; }
.remote-decision-run-summary p { margin: 8px 0; color: #bac8d9; font-size: 13px; line-height: 1.5; }
.remote-decision-run-meta { color: #8499b5; font-size: 11px; }
.remote-decision-run-meta a { margin-left: auto; color: #9fe3c8; text-decoration: none; }
.remote-stop-button { margin-top: 10px; border: 0; background: transparent; color: #e8a6a6; cursor: pointer; font-size: 12px; padding: 0; text-align: left; }
.remote-rerun-button { margin-top: 10px; border: 0; background: transparent; color: #9fe3c8; cursor: pointer; font-size: 12px; padding: 0; text-align: left; }
.remote-rerun-button:disabled { cursor: wait; opacity: .6; }
.remote-confirm-stop { background: #a45252; }
.remote-notable-cards { display: grid; gap: 6px; margin-top: 12px; }
.remote-notable-cards a { display: grid; grid-template-columns: 30px 58px 1fr; align-items: center; gap: 6px; padding: 7px 8px; border-radius: 7px; background: rgba(255,255,255,.04); color: inherit; font-size: 11px; text-decoration: none; }
.remote-notable-cards a:hover { background: rgba(118, 230, 192, .12); }
.remote-notable-cards b { color: #8eddbb; }
.remote-notable-cards span { color: #b6c5d8; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.remote-decision-history { padding: 14px; border: 1px solid rgba(120, 150, 184, .24); border-radius: 11px; background: rgba(15, 31, 51, .55); }
.remote-decision-history header { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 8px; }
.remote-decision-history header strong { color: #91a4c0; font-size: 11px; font-weight: 500; }
.remote-history-item { display: grid; grid-template-columns: 112px 1fr 18px; align-items: center; gap: 8px; padding: 9px 0; border-top: 1px solid rgba(130, 154, 189, .12); color: inherit; text-decoration: none; }
.remote-history-item:hover { color: #b8e9d4; }
.remote-history-item div { display: grid; gap: 3px; }
.remote-history-item strong { font-size: 12px; }
.remote-history-item small, .remote-history-item span { color: #91a4c0; font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.remote-history-item b { color: #9fe3c8; }
</style>
