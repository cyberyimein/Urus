<script setup lang="ts">
import { computed, ref } from 'vue'

import type { DecisionTraceGraph, RawModelTurn, RawResponsePayload, TraceNode, TraceNodeDetail } from '@/types/research'

const props = defineProps<{
  trace: DecisionTraceGraph | null
  selectedNode: TraceNodeDetail | null
  rawResponse: RawResponsePayload | null
  loading: boolean
  rawError: string
  status?: string
  errorMessage?: string | null
}>()

const emit = defineEmits<{
  (event: 'select-node', nodeId: string): void
  (event: 'load-raw'): void
  (event: 'focus-evidence', path: string): void
}>()

const showRaw = ref(false)
const zoom = ref(1)
const pan = ref({ x: 0, y: 0 })
const dragging = ref(false)
const dragStart = ref({ x: 0, y: 0, panX: 0, panY: 0 })
const laneNames = computed(() => [...new Set((props.trace?.nodes ?? []).map((node) => node.lane))])
const nodesByLane = computed(() => {
  const grouped = new Map<string, TraceNode[]>()
  for (const node of props.trace?.nodes ?? []) {
    const nodes = grouped.get(node.lane) ?? []
    nodes.push(node)
    grouped.set(node.lane, nodes)
  }
  for (const nodes of grouped.values()) nodes.sort((left, right) => left.sequence - right.sequence)
  return grouped
})
const nodePositions = computed(() => {
  const positions = new Map<string, { x: number; y: number; width: number; height: number }>()
  for (const [laneIndex, lane] of laneNames.value.entries()) {
    const nodes = nodesByLane.value.get(lane) ?? []
    for (const [index, node] of nodes.entries()) positions.set(node.id, { x: 32 + index * 224, y: 54 + laneIndex * 142, width: 190, height: 94 })
  }
  return positions
})
const graphWidth = computed(() => Math.max(720, ...[...nodePositions.value.values()].map((position) => position.x + position.width + 32)))
const graphHeight = computed(() => Math.max(420, laneNames.value.length * 142 + 35))
const edges = computed(() => (props.trace?.edges ?? []).filter((edge) => nodePositions.value.has(edge.from) && nodePositions.value.has(edge.to)))
const rationale = computed(() => {
  const output = props.selectedNode?.output_summary ?? {}
  const value = output.decision_rationale ?? output.rationale ?? output.reasoning_summary ?? output.summary
  if (typeof value === 'string') return value
  if (Array.isArray(value)) return value.map((item) => String(item)).join('\n')
  return ''
})

function nodeStatus(node: { status: string }): string { return node.status || 'unknown' }
function json(value: unknown): string { return JSON.stringify(value ?? {}, null, 2) }
function evidencePath(value: Record<string, unknown>): string { return typeof value.path === 'string' ? value.path : '' }
function safeText(value: unknown): string { return value === null || value === undefined ? '' : typeof value === 'string' ? value : JSON.stringify(value, null, 2) }

function reasoningValues(value: unknown, path = ''): Array<{ path: string; value: string }> {
  const found: Array<{ path: string; value: string }> = []
  const keys = new Set(['reasoning', 'reasoning_content', 'reasoning_details', 'analysis', 'thinking', 'chain_of_thought'])
  function walk(current: unknown, currentPath: string) {
    if (Array.isArray(current)) {
      current.slice(0, 20).forEach((item, index) => walk(item, `${currentPath}[${index}]`))
      return
    }
    if (!current || typeof current !== 'object') return
    for (const [key, child] of Object.entries(current as Record<string, unknown>)) {
      const nextPath = currentPath ? `${currentPath}.${key}` : key
      if (keys.has(key.toLowerCase().replaceAll('-', '_'))) {
        const text = safeText(child)
        if (text) found.push({ path: nextPath, value: text })
      }
      walk(child, nextPath)
    }
  }
  walk(value, path)
  return found
}
const rawReasoning = computed(() => (props.rawResponse?.model_turns ?? []).flatMap((turn) => reasoningValues({ message: turn.response_message, provider: turn.raw_provider_response })))
const rawFinalMessages = computed(() => (props.rawResponse?.model_turns ?? []).map((turn) => ({ sequence: turn.sequence, content: turn.response_message?.content ?? turn.response_message })))

function toggleRaw() {
  showRaw.value = !showRaw.value
  if (showRaw.value && !props.rawResponse) emit('load-raw')
}
function selectNode(nodeId: string) { showRaw.value = false; emit('select-node', nodeId) }
function setZoom(value: number) { zoom.value = Math.min(1.6, Math.max(0.7, Number(value.toFixed(2)))) }
function startPan(event: PointerEvent) {
  if ((event.target as HTMLElement).closest('button')) return
  dragging.value = true
  dragStart.value = { x: event.clientX, y: event.clientY, panX: pan.value.x, panY: pan.value.y }
  ;(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)
}
function movePan(event: PointerEvent) {
  if (!dragging.value) return
  pan.value = { x: dragStart.value.panX + event.clientX - dragStart.value.x, y: dragStart.value.panY + event.clientY - dragStart.value.y }
}
function endPan(event: PointerEvent) { dragging.value = false; (event.currentTarget as HTMLElement).releasePointerCapture?.(event.pointerId) }
function fitCanvas() { zoom.value = 1; pan.value = { x: 0, y: 0 } }
function edgePoint(edge: { from: string; to: string }, side: 'from' | 'to'): { x: number; y: number } {
  const node = nodePositions.value.get(side === 'from' ? edge.from : edge.to)
  if (!node) return { x: 0, y: 0 }
  return side === 'from' ? { x: node.x + node.width, y: node.y + node.height / 2 } : { x: node.x, y: node.y + node.height / 2 }
}
</script>

<template>
  <div v-if="!trace" class="empty-panel">
    <p v-if="status === 'disabled' || status === 'technical_ready'">Urus Agent 未启用，没有可复盘的决策节点。</p>
    <p v-else-if="status === 'running'">决策仍在运行，复盘轨迹尚未完成。</p>
    <p v-else-if="errorMessage">复盘轨迹不可用：{{ errorMessage }}</p>
    <p v-else>复盘轨迹尚未加载。</p>
  </div>
  <template v-else>
    <div class="report-toolbar"><span class="live-badge">只读决策轨迹 · {{ trace.nodes.length }} nodes</span><span class="subtle">真实节点和依赖边；点击节点查看输入、工具调用、理由和原始返回</span></div>
    <section class="trace-layout">
      <div class="trace-canvas" :class="{ dragging }" aria-label="AI 决策节点图" @pointerdown="startPan" @pointermove="movePan" @pointerup="endPan" @pointercancel="endPan">
        <div class="trace-canvas-controls" @pointerdown.stop><button type="button" class="secondary-button" @click="setZoom(zoom - 0.1)">−</button><button type="button" class="secondary-button trace-zoom-value" @click="fitCanvas">{{ Math.round(zoom * 100) }}%</button><button type="button" class="secondary-button" @click="setZoom(zoom + 0.1)">＋</button><button type="button" class="secondary-button" @click="fitCanvas">适配</button></div>
        <div class="trace-graph-viewport"><div class="trace-graph" :style="{ width: `${graphWidth}px`, height: `${graphHeight}px`, transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }">
          <svg class="trace-graph-edges" :viewBox="`0 0 ${graphWidth} ${graphHeight}`" :width="graphWidth" :height="graphHeight" aria-hidden="true"><defs><marker id="trace-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="currentColor" /></marker></defs><line v-for="edge in edges" :key="`${edge.from}-${edge.to}-${edge.kind}`" :x1="edgePoint(edge, 'from').x" :y1="edgePoint(edge, 'from').y" :x2="edgePoint(edge, 'to').x" :y2="edgePoint(edge, 'to').y" :class="`trace-edge trace-edge-${edge.kind}`" marker-end="url(#trace-arrow)" /></svg>
          <div v-for="(lane, laneIndex) in laneNames" :key="lane" class="trace-graph-lane" :style="{ top: `${54 + laneIndex * 142 - 34}px` }"><span>{{ lane }}</span><small>{{ (nodesByLane.get(lane) ?? []).length }} nodes</small></div>
          <button v-for="node in trace.nodes" :key="node.id" type="button" class="trace-node trace-graph-node" :class="{ active: selectedNode?.id === node.id }" :data-status="nodeStatus(node)" :style="{ left: `${nodePositions.get(node.id)?.x ?? 0}px`, top: `${nodePositions.get(node.id)?.y ?? 0}px` }" @click="selectNode(node.id)"><span class="trace-node-type">{{ node.node_type }}</span><strong>{{ node.label }}</strong><small>{{ nodeStatus(node) }} · #{{ node.sequence }}</small></button>
        </div></div>
        <div class="trace-graph-legend"><span><i class="edge-parent"></i>parent</span><span><i class="edge-dependency"></i>dependency</span><span>拖动空白区域平移</span></div>
      </div>

      <aside class="trace-inspector">
        <div v-if="loading" class="empty-panel"><p>读取节点…</p></div>
        <template v-else-if="selectedNode">
          <div class="trace-inspector-heading"><div><p class="eyebrow">NODE INSPECTOR</p><h2>{{ selectedNode.label }}</h2></div><span class="status-badge" :data-status="selectedNode.status">{{ selectedNode.status }}</span></div>
          <div class="inline-facts"><span>lane {{ selectedNode.lane }}</span><span>type {{ selectedNode.node_type }}</span><span>#{{ selectedNode.sequence }}</span></div>
          <div v-if="rationale" class="trace-rationale"><p class="eyebrow">DECISION RATIONALE</p><p>{{ rationale }}</p><small>这是 Agent 输出的结构化理由；不是未返回的隐藏思考。</small></div>
          <div v-else class="trace-rationale trace-rationale-empty"><p class="eyebrow">DECISION RATIONALE</p><p>该节点没有结构化理由摘要。</p></div>
          <div v-if="selectedNode.decision_run" class="trace-run-meta"><span>provider {{ selectedNode.decision_run.provider }}</span><span>model {{ selectedNode.decision_run.model || '—' }}</span><span>tools {{ selectedNode.decision_run.tool_call_count }}</span><span>prefetch {{ selectedNode.decision_run.prefetched_tool_count ?? 0 }}</span><span>model tools {{ selectedNode.decision_run.model_requested_tool_count ?? 0 }}</span><span>temperature {{ selectedNode.decision_run.temperature ?? '—' }}</span><span>tokens {{ selectedNode.decision_run.prompt_tokens ?? 0 }}/{{ selectedNode.decision_run.completion_tokens ?? 0 }}</span><span>cache {{ selectedNode.decision_run.cached_prompt_tokens ?? 0 }} · {{ selectedNode.decision_run.cache_hit_rate != null ? `${(selectedNode.decision_run.cache_hit_rate * 100).toFixed(2)}%` : '—' }}</span><span v-if="selectedNode.decision_run.estimated_cost != null">cost ${{ selectedNode.decision_run.estimated_cost.toFixed(6) }}</span></div>
          <details open><summary>输入摘要</summary><pre class="compact-json">{{ json(selectedNode.input_summary) }}</pre></details>
          <details open><summary>输出摘要</summary><pre class="compact-json">{{ json(selectedNode.output_summary) }}</pre></details>
          <details v-if="selectedNode.tool_calls?.length"><summary>工具调用（{{ selectedNode.tool_calls.length }}）</summary><div class="trace-tool-list"><div v-for="call in selectedNode.tool_calls" :key="String(call.sequence)" class="trace-tool-row"><strong>{{ String(call.tool_name) }}</strong><span>{{ call.ok ? 'ok' : 'failed' }} · {{ String(call.duration_ms ?? '—') }} ms</span><pre class="compact-json">{{ json(call.arguments) }}</pre></div></div></details>
          <div v-if="selectedNode.evidence_refs?.length" class="evidence-links"><span class="subtle">Evidence Reference</span><template v-for="reference in selectedNode.evidence_refs" :key="evidencePath(reference)"><button v-if="evidencePath(reference)" type="button" class="evidence-link" @click="emit('focus-evidence', evidencePath(reference))">{{ String(reference.observation ?? reference.path ?? 'evidence') }}</button></template></div>
          <div v-if="selectedNode.error_message" class="report-error">{{ selectedNode.error_code }} · {{ selectedNode.error_message }}</div>
          <div v-if="selectedNode.decision_run" class="raw-response-panel"><button class="secondary-button raw-toggle" type="button" @click="toggleRaw">{{ showRaw ? '收起 LLM 原始返回' : '展开 LLM 原始返回' }}</button><p class="subtle">默认不主动展示。只有供应商实际返回的 reasoning / analysis / thinking 才会出现在“模型推理字段”里，不会伪造缺失的思考过程。</p><p v-if="rawError" class="report-error">{{ rawError }}</p><div v-if="showRaw && rawResponse" class="raw-response-content"><p class="notice-inline">{{ rawResponse.warning }}</p><details open><summary>最终消息</summary><div v-for="turn in rawFinalMessages" :key="turn.sequence" class="raw-turn"><span class="subtle">turn #{{ turn.sequence }}</span><pre class="compact-json">{{ json(turn.content) }}</pre></div></details><details v-if="rawReasoning.length"><summary>模型实际返回的推理字段（{{ rawReasoning.length }}）</summary><div v-for="item in rawReasoning" :key="item.path" class="reasoning-block"><span class="subtle">{{ item.path }}</span><pre>{{ item.value }}</pre></div></details><details><summary>完整供应商返回（原始 JSON）</summary><div v-for="turn in rawResponse.model_turns" :key="turn.sequence" class="raw-turn"><span class="subtle">turn #{{ turn.sequence }}<template v-if="turn.returned_reasoning_fields?.length"> · 实际返回字段：{{ turn.returned_reasoning_fields.join(', ') }}</template><template v-if="turn.raw_response_truncated"> · 已截断</template></span><pre class="compact-json">{{ json(turn) }}</pre></div></details></div></div>
        </template>
        <div v-else class="empty-panel"><p>请选择一个节点。</p></div>
      </aside>
    </section>
  </template>
</template>
