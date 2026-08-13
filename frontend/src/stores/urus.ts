import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { api, ApiError } from '@/api/client'
import type { FrontendReadModel, RunDetail, RunListItem, RunType } from '@/types/api'

export const useUrusStore = defineStore('urus', () => {
  const connection = ref<'unknown' | 'connected' | 'offline'>('unknown')
  const runs = ref<RunListItem[]>([])
  const latestRun = ref<RunDetail | null>(null)
  const selectedRun = ref<RunDetail | null>(null)
  const latestReadModel = ref<FrontendReadModel | null>(null)
  const selectedReadModel = ref<FrontendReadModel | null>(null)
  const busy = ref(false)
  const error = ref('')

  const latestRunLabel = computed(() => latestRun.value?.run_type ?? '')

  function setError(reason: unknown) {
    error.value = reason instanceof ApiError ? reason.message : '请求失败，请稍后重试。'
  }

  async function loadDashboard() {
    error.value = ''
    try {
      const [, nextRuns] = await Promise.all([api.health(), api.listRuns()])
      connection.value = 'connected'
      runs.value = nextRuns
      const firstReadable = nextRuns.find((run) => Boolean(run.snapshot_id))
      if (!firstReadable) {
        latestRun.value = null
        latestReadModel.value = null
        return
      }
      await loadRun(firstReadable.id, false)
      latestRun.value = selectedRun.value
      latestReadModel.value = selectedReadModel.value
    } catch (reason) {
      connection.value = 'offline'
      setError(reason)
    }
  }

  async function loadRuns() {
    try {
      runs.value = await api.listRuns()
      connection.value = 'connected'
    } catch (reason) {
      connection.value = 'offline'
      setError(reason)
    }
  }

  async function loadRun(runId: string, surfaceError = true) {
    if (surfaceError) error.value = ''
    try {
      const detail = await api.getRun(runId)
      selectedRun.value = detail
      selectedReadModel.value = detail.snapshot_id
        ? await api.getFrontendReadModel(detail.snapshot_id)
        : null
      connection.value = 'connected'
      return detail
    } catch (reason) {
      if (surfaceError) setError(reason)
      connection.value = 'offline'
      return null
    }
  }

  async function triggerRun(
    runType: RunType,
    options: { simulateMacroEvent: boolean; simulateInstrumentEvent: boolean },
  ) {
    busy.value = true
    error.value = ''
    try {
      const created = await api.createRun({
        run_type: runType,
        simulate_macro_event: options.simulateMacroEvent,
        simulate_instrument_event: options.simulateInstrumentEvent,
      })
      await loadRuns()
      const detail = await loadRun(created.run_id)
      latestRun.value = detail
      latestReadModel.value = selectedReadModel.value
      return detail
    } catch (reason) {
      setError(reason)
      return null
    } finally {
      busy.value = false
    }
  }

  return {
    connection,
    runs,
    latestRun,
    selectedRun,
    latestReadModel,
    selectedReadModel,
    latestRunLabel,
    busy,
    error,
    loadDashboard,
    loadRuns,
    loadRun,
    triggerRun,
  }
})
