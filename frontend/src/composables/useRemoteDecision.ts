import { onUnmounted, ref } from 'vue'

import { api, ApiError } from '@/api/client'
import type {
  RemoteDecisionIntent,
  RemoteDecisionPreflight,
  RemoteDecisionRun,
  RemoteDecisionSource,
} from '@/types/remoteDecision'

const TERMINAL = new Set(['accepted', 'rejected_result', 'failed', 'stopped'])

function restoreQuery(intentType: RemoteDecisionIntent, source: RemoteDecisionSource) {
  if (intentType === 'instrument_arbitration' && source.symbol && source.dataset_id) {
    return {
      scope_type: 'instrument',
      scope_id: source.symbol,
      dataset_id: source.dataset_id,
    }
  }
  if (intentType === 'group_arbitration' && source.dataset_id) {
    return { scope_type: 'group', dataset_id: source.dataset_id }
  }
  if ((intentType === 'indicator_attention' || intentType === 'strategy_attention') && source.observation_run_id) {
    return { scope_type: 'observation_run', scope_id: source.observation_run_id }
  }
  return null
}

function sourceMatches(run: RemoteDecisionRun, intentType: RemoteDecisionIntent, source: RemoteDecisionSource) {
  if (run.intent_type !== intentType) return false
  return Object.entries(source).every(([key, value]) => {
    if (value === undefined || value === null || value === '') return true
    return String(run.source?.[key as keyof RemoteDecisionSource] ?? '') === String(value)
  })
}

function isNewer(candidate: RemoteDecisionRun, current: RemoteDecisionRun) {
  const candidateTime = Date.parse(candidate.created_at)
  const currentTime = Date.parse(current.created_at)
  if (Number.isFinite(candidateTime) && Number.isFinite(currentTime) && candidateTime !== currentTime) {
    return candidateTime > currentTime
  }
  return candidate.local_run_id !== current.local_run_id
}

export function useRemoteDecision() {
  const preflight = ref<RemoteDecisionPreflight | null>(null)
  const run = ref<RemoteDecisionRun | null>(null)
  const loading = ref(false)
  const error = ref('')
  let timer: ReturnType<typeof setInterval> | null = null
  let stateGeneration = 0

  async function prepare(intentType: RemoteDecisionIntent, source: RemoteDecisionSource) {
    const generation = stateGeneration
    loading.value = true
    error.value = ''
    try {
      const next = await api.preflightRemoteDecision({ intent_type: intentType, source })
      if (generation === stateGeneration) preflight.value = next
      return generation === stateGeneration ? next : null
    } catch (reason) {
      if (generation !== stateGeneration) return null
      error.value = reason instanceof Error ? reason.message : 'AI 评估准备失败。'
      throw reason
    } finally {
      if (generation === stateGeneration) loading.value = false
    }
  }

  async function submit(intentType: RemoteDecisionIntent, source: RemoteDecisionSource, requestIntentId: string) {
    if (!preflight.value?.enabled || !preflight.value.preflight_fingerprint) {
      error.value = preflight.value?.blockers[0]?.message ?? '当前冻结证据不可发起 AI 评估。'
      return null
    }
    loading.value = true
    error.value = ''
    const generation = stateGeneration
    try {
      const next = await api.submitRemoteDecision({
        intent_type: intentType,
        source,
        preflight_fingerprint: preflight.value.preflight_fingerprint,
        request_intent_id: requestIntentId,
      })
      if (generation === stateGeneration) {
        run.value = next
        startPolling()
      }
      return generation === stateGeneration ? next : null
    } catch (reason) {
      const apiError = reason instanceof ApiError ? reason : null
      if (generation === stateGeneration) {
        error.value = apiError?.message ?? (reason instanceof Error ? reason.message : 'AI 评估提交失败。')
      }
      throw reason
    } finally {
      if (generation === stateGeneration) loading.value = false
    }
  }

  async function refresh(localRunId = run.value?.local_run_id) {
    if (!localRunId) return null
    const generation = stateGeneration
    try {
      const next = await api.getRemoteDecision(localRunId)
      if (generation !== stateGeneration) return null
      run.value = next
      error.value = ''
      if (TERMINAL.has(next.status)) stopPolling()
      return next
    } catch (reason) {
      if (generation === stateGeneration) error.value = reason instanceof Error ? reason.message : 'AI 运行状态加载失败。'
      return null
    }
  }

  /**
   * Restore the most recent persisted run for the currently displayed frozen
   * evidence. The panel is intentionally local state, so navigating to the
   * run detail and back creates a new composable instance; without this lookup
   * the completed decision disappears even though it is still in the API.
   */
  async function restore(intentType: RemoteDecisionIntent, source: RemoteDecisionSource) {
    const query = restoreQuery(intentType, source)
    if (!query) return null
    const generation = stateGeneration
    const runIdAtRequest = run.value?.local_run_id ?? null
    try {
      const candidates = await api.listRemoteDecisions({ ...query, limit: 50 })
      if (generation !== stateGeneration) return null
      const next = candidates.find((candidate) => sourceMatches(candidate, intentType, source))
      if (!next) return null
      // A user may submit while the history request is in flight. Keep the
      // freshly submitted run instead of letting a stale list response win.
      if (run.value && run.value.local_run_id !== runIdAtRequest) return run.value
      if (run.value && run.value.local_run_id !== next.local_run_id && !isNewer(next, run.value)) {
        return run.value
      }
      run.value = next
      if (TERMINAL.has(next.status)) stopPolling()
      else startPolling()
      return next
    } catch {
      // Hydration is supplementary to the deterministic evidence page. A
      // transient history request failure should not hide the page itself.
      return null
    }
  }

  async function stop() {
    if (!run.value) return null
    const generation = stateGeneration
    loading.value = true
    error.value = ''
    try {
      const next = await api.stopRemoteDecision(run.value.local_run_id)
      if (generation === stateGeneration) {
        run.value = next
        startPolling()
      }
      return generation === stateGeneration ? next : null
    } catch (reason) {
      if (generation === stateGeneration) error.value = reason instanceof Error ? reason.message : '停止 AI 运行失败。'
      return null
    } finally {
      if (generation === stateGeneration) loading.value = false
    }
  }

  async function rerun(requestIntentId = crypto.randomUUID()) {
    if (!run.value) return null
    const generation = stateGeneration
    loading.value = true
    error.value = ''
    try {
      const next = await api.rerunRemoteDecision(run.value.local_run_id, requestIntentId)
      if (generation === stateGeneration) {
        run.value = next
        startPolling()
      }
      return generation === stateGeneration ? next : null
    } catch (reason) {
      if (generation === stateGeneration) error.value = reason instanceof Error ? reason.message : 'AI 重新运行失败。'
      return null
    } finally {
      if (generation === stateGeneration) loading.value = false
    }
  }

  function reset() {
    stateGeneration += 1
    stopPolling()
    preflight.value = null
    run.value = null
    loading.value = false
    error.value = ''
  }

  function startPolling() {
    stopPolling()
    timer = setInterval(() => { void refresh() }, 1500)
  }

  function stopPolling() {
    if (timer !== null) {
      clearInterval(timer)
      timer = null
    }
  }

  onUnmounted(stopPolling)
  return { preflight, run, loading, error, prepare, submit, refresh, restore, stop, rerun, reset, startPolling, stopPolling }
}
