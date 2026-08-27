import { onUnmounted, ref } from 'vue'

import { api, ApiError } from '@/api/client'
import type {
  RemoteDecisionIntent,
  RemoteDecisionPreflight,
  RemoteDecisionRun,
  RemoteDecisionSource,
} from '@/types/remoteDecision'

const TERMINAL = new Set(['accepted', 'rejected_result', 'failed', 'stopped'])

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
  return { preflight, run, loading, error, prepare, submit, refresh, stop, rerun, reset, startPolling, stopPolling }
}
