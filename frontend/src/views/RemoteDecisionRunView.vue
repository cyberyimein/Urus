<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import AppShell from '@/components/AppShell.vue'
import { api } from '@/api/client'
import type { RemoteDecisionEvent, RemoteDecisionRun } from '@/types/remoteDecision'

const route = useRoute()
const run = ref<RemoteDecisionRun | null>(null)
const events = ref<RemoteDecisionEvent[]>([])
const loading = ref(true)
const error = ref('')
let timer: ReturnType<typeof setInterval> | null = null
let requestInFlight = false

const runId = computed(() => String(route.params.localRunId ?? ''))
const returnTo = computed(() => {
  const target = route.query.return_to
  // Only accept an in-app absolute path. This keeps the convenience link from
  // becoming an open redirect when a run URL is shared or modified.
  return typeof target === 'string' && target.startsWith('/') && !target.startsWith('//') ? target : '/'
})

async function load() {
  if (!runId.value || requestInFlight) return
  requestInFlight = true
  try {
    const [nextRun, nextEvents] = await Promise.all([
      api.getRemoteDecision(runId.value),
      api.getRemoteDecisionEvents(runId.value, events.value.at(-1)?.sequence ?? 0),
    ])
    run.value = nextRun
    if (nextEvents.length) events.value = [...events.value, ...nextEvents]
    error.value = ''
    if (['accepted', 'rejected_result', 'failed', 'stopped'].includes(nextRun.status)) stopPolling()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : 'Remote Decision 加载失败。'
  } finally {
    requestInFlight = false
    loading.value = false
  }
}

function startPolling() {
  stopPolling()
  timer = setInterval(() => { void load() }, 1500)
}

function stopPolling() {
  if (timer !== null) { clearInterval(timer); timer = null }
}

onMounted(() => { void load(); startPolling() })
onUnmounted(stopPolling)
</script>

<template>
  <AppShell />
  <main class="remote-run-page">
    <RouterLink class="remote-run-back" :to="returnTo">← 返回决策工作台</RouterLink>
    <section v-if="loading" class="empty-panel"><p>正在读取 AI Workflow Run…</p></section>
    <section v-else-if="error" class="error-banner" role="alert">{{ error }}</section>
    <template v-else-if="run">
      <header class="remote-run-header">
        <div><p class="eyebrow">REMOTE DECISION / {{ run.intent_type }}</p><h1>{{ run.status }}</h1><p>{{ run.workflow_ref }} · {{ run.local_run_id }}</p></div>
        <span class="remote-run-state" :data-status="run.status">{{ run.validation_status }}</span>
      </header>
      <section class="remote-run-grid">
        <article class="remote-run-card"><span>冻结输入</span><strong>{{ run.input_sha256 }}</strong><small>{{ run.scope_type }} / {{ run.scope_id }}</small></article>
        <article class="remote-run-card"><span>Anomalo Run</span><strong>{{ run.anomalo_run_id ?? '等待提交' }}</strong><small>events {{ run.latest_event_sequence }}</small></article>
        <article class="remote-run-card"><span>结果</span><strong>{{ run.result ? '已验收' : run.safe_error_message ?? '等待结果' }}</strong><small>{{ run.artifact?.artifact_sha256 ?? '—' }}</small></article>
      </section>
      <section v-if="run.result" class="remote-run-result"><p class="section-kicker">ACCEPTED ARTIFACT</p><h2>{{ run.result.summary ?? 'AI 结果' }}</h2><div v-for="card in run.result.notable_cards ?? []" :key="String(card.card_id)" class="remote-run-card-row"><b>#{{ card.rank }}</b><strong>{{ card.symbol ?? card.card_id }}</strong><span>{{ card.why_notable ?? card.finding_type ?? '值得关注' }}</span></div></section>
      <section class="remote-run-events"><div class="section-kicker">DECISION TRACE</div><div v-for="event in events" :key="event.sequence" class="remote-run-event"><b>{{ event.sequence }}</b><strong>{{ event.event_type }}</strong><span>{{ event.node_id ?? '' }}</span><small>{{ event.event_timestamp ?? event.created_at }}</small></div></section>
    </template>
  </main>
</template>

<style scoped>
.remote-run-page { max-width: 1120px; margin: 0 auto; padding: 42px 26px 80px; color: #eff4fc; }
.remote-run-back { color: #a9dec8; text-decoration: none; font-size: 13px; }
.remote-run-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; margin: 30px 0; }
.remote-run-header h1 { margin: 5px 0; text-transform: uppercase; }
.remote-run-header p { color: #9cadc5; }
.remote-run-state { padding: 7px 11px; border-radius: 999px; background: rgba(103, 224, 174, .12); color: #99e6c4; font-size: 11px; text-transform: uppercase; }
.remote-run-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.remote-run-card, .remote-run-result, .remote-run-events { padding: 18px; border: 1px solid rgba(130, 154, 189, .22); border-radius: 14px; background: rgba(20, 35, 56, .65); }
.remote-run-card { display: grid; gap: 8px; }
.remote-run-card span, .remote-run-card small { color: #91a4c0; font-size: 11px; }
.remote-run-card strong { word-break: break-word; font-size: 13px; }
.remote-run-result { margin-top: 14px; }
.remote-run-result h2 { margin: 8px 0 15px; }
.remote-run-card-row { display: grid; grid-template-columns: 34px 70px 1fr; gap: 8px; padding: 9px 0; border-top: 1px solid rgba(130, 154, 189, .15); font-size: 13px; }
.remote-run-card-row span { color: #b6c5d8; }
.remote-run-events { margin-top: 14px; }
.remote-run-event { display: grid; grid-template-columns: 34px 1fr 120px 190px; gap: 8px; padding: 10px 0; border-bottom: 1px solid rgba(130, 154, 189, .12); font-size: 12px; }
.remote-run-event span, .remote-run-event small { color: #91a4c0; }
@media (max-width: 760px) { .remote-run-grid { grid-template-columns: 1fr; } .remote-run-event { grid-template-columns: 30px 1fr; } .remote-run-event span, .remote-run-event small { grid-column: 2; } }
</style>
