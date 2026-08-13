import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { api, ApiError } from '@/api/client'
import type {
  DecisionReport,
  DecisionTraceGraph,
  RawResponsePayload,
  ResearchReportIndex,
  ResearchReportPayload,
  TechnicalReport,
  TraceNodeDetail,
} from '@/types/research'
import type { FrontendReadModel } from '@/types/api'

export const useResearchReportStore = defineStore('research-report', () => {
  const report = ref<ResearchReportPayload | null>(null)
  const available = ref<ResearchReportIndex[]>([])
  const technical = ref<TechnicalReport | null>(null)
  const decision = ref<DecisionReport | null>(null)
  const trace = ref<DecisionTraceGraph | null>(null)
  const selectedNode = ref<TraceNodeDetail | null>(null)
  const rawResponse = ref<RawResponsePayload | null>(null)
  const activeTab = ref<'technical' | 'decision' | 'trace'>('technical')
  const loading = ref(false)
  const nodeLoading = ref(false)
  const error = ref('')
  const rawError = ref('')

  const reportId = computed(() => report.value?.report_id ?? '')
  const hasReport = computed(() => Boolean(report.value))

  function clear() {
    report.value = null
    available.value = []
    technical.value = null
    decision.value = null
    trace.value = null
    selectedNode.value = null
    rawResponse.value = null
    error.value = ''
    rawError.value = ''
  }

  function setError(reason: unknown) {
    error.value = reason instanceof ApiError ? reason.message : '研究报告请求失败，请稍后重试。'
  }

  async function loadForRun(runId: string, preferredReportId = '') {
    clear()
    if (!runId) return null
    loading.value = true
    try {
      available.value = await api.listResearchReports(runId)
      const latest =
        available.value.find((item) => item.report_id === preferredReportId) ??
        available.value.find((item) => item.status === 'succeeded' || item.status === 'partial') ??
        available.value[0]
      if (!latest) return await loadDisabledRun(runId)
      return await loadReport(latest.report_id)
    } catch (reason) {
      setError(reason)
      return null
    } finally {
      loading.value = false
    }
  }

  async function loadReport(id: string) {
    if (!id) return null
    loading.value = true
    error.value = ''
    try {
      report.value = await api.getResearchReport(id)
      technical.value = null
      decision.value = null
      // Each tab is fetched only when it is first needed. The graph is the
      // largest payload and is therefore never part of the metadata request.
      if (activeTab.value === 'technical') {
        try {
          technical.value = await api.getTechnicalReport(id)
        } catch (reason) {
          setError(reason)
        }
      }
      if (activeTab.value === 'decision') {
        try {
          if (!technical.value) technical.value = await api.getTechnicalReport(id)
        } catch (reason) {
          setError(reason)
        }
        try {
          decision.value = await api.getDecisionReport(id)
        } catch (reason) {
          if (!(reason instanceof ApiError && reason.status === 409)) setError(reason)
        }
      }
      if (activeTab.value === 'trace') await loadTrace()
      return report.value
    } catch (reason) {
      setError(reason)
      return null
    } finally {
      loading.value = false
    }
  }

  async function loadDisabledRun(runId: string) {
    try {
      const run = await api.getRun(runId)
      if (!run.snapshot_id) return null
      const readModel = await api.getFrontendReadModel(run.snapshot_id) as FrontendReadModel
      const technicalReport = (readModel.technical_report ?? {}) as TechnicalReport
      const disabled: ResearchReportPayload = {
        report_id: `disabled-${runId}`,
        session_id: null,
        workflow_run_id: runId,
        dataset_key: `snapshot:${run.snapshot_id}`,
        cutoff_time: readModel.cutoff_time,
        status: 'technical_ready',
        quality: technicalReport.quality ?? {},
        policy: { enabled: false, reason: 'Stage 4B AI 未启用' },
        technical_report_schema_version: String(technicalReport.schema_version ?? 'urus.technical_report.v1'),
        decision_report_schema_version: null,
        equity_decision_run_id: null,
        error_code: 'urus_agent_disabled',
        error_message: 'Urus Agent 当前未启用；技术整理报告仍可查看。',
        started_at: null,
        completed_at: null,
        created_at: readModel.generated_at,
        run_summary: { run_count: 0, tool_call_count: 0, prompt_tokens: 0, completion_tokens: 0, duration_ms: 0, providers: [], models: [], skill_hashes: [], statuses: ['disabled'] },
        technical_report: technicalReport,
        decision_report: null,
        trace_summary: { node_count: 0, model_run_count: 0 },
      }
      report.value = disabled
      available.value = [disabled]
      technical.value = technicalReport
      decision.value = null
      trace.value = null
      return disabled
    } catch (reason) {
      setError(reason)
      return null
    }
  }

  async function selectTab(tab: 'technical' | 'decision' | 'trace') {
    activeTab.value = tab
    if (tab === 'technical' && !technical.value && reportId.value) {
      technical.value = await api.getTechnicalReport(reportId.value)
    }
    if (tab === 'decision' && !decision.value && reportId.value) {
      try {
        if (!technical.value) technical.value = await api.getTechnicalReport(reportId.value)
      } catch (reason) {
        setError(reason)
      }
      try {
        decision.value = await api.getDecisionReport(reportId.value)
      } catch (reason) {
        if (!(reason instanceof ApiError && reason.status === 409)) setError(reason)
      }
    }
    if (tab === 'trace') await loadTrace()
  }

  async function loadTrace() {
    if (!reportId.value || trace.value) return trace.value
    nodeLoading.value = true
    try {
      trace.value = await api.getDecisionTrace(reportId.value)
      const first = trace.value.nodes[0]
      if (first) await selectNode(first.id)
      return trace.value
    } catch (reason) {
      setError(reason)
      return null
    } finally {
      nodeLoading.value = false
    }
  }

  async function selectNode(nodeId: string) {
    if (!reportId.value || !nodeId) return null
    nodeLoading.value = true
    rawResponse.value = null
    rawError.value = ''
    try {
      selectedNode.value = await api.getTraceNode(reportId.value, nodeId)
      return selectedNode.value
    } catch (reason) {
      setError(reason)
      return null
    } finally {
      nodeLoading.value = false
    }
  }

  async function loadRawResponse() {
    if (!reportId.value || !selectedNode.value?.id) return null
    nodeLoading.value = true
    rawError.value = ''
    try {
      rawResponse.value = await api.getTraceNodeRawResponse(reportId.value, selectedNode.value.id)
      return rawResponse.value
    } catch (reason) {
      rawError.value = reason instanceof ApiError ? reason.message : '原始模型返回加载失败。'
      return null
    } finally {
      nodeLoading.value = false
    }
  }

  return {
    report,
    available,
    technical,
    decision,
    trace,
    selectedNode,
    rawResponse,
    activeTab,
    loading,
    nodeLoading,
    error,
    rawError,
    reportId,
    hasReport,
    clear,
    loadForRun,
    loadReport,
    loadDisabledRun,
    selectTab,
    loadTrace,
    selectNode,
    loadRawResponse,
  }
})
