<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue'

import type { ResearchReportPayload } from '@/types/research'
import { formatDate, formatNumber } from '@/utils/format'

const props = defineProps<{
  report: ResearchReportPayload
  open: boolean
}>()

const emit = defineEmits<{
  (event: 'close'): void
}>()
const runSummary = computed(() => props.report.run_summary)

function closeOnEscape(event: KeyboardEvent) {
  if (event.key === 'Escape' && props.open) emit('close')
}

onMounted(() => window.addEventListener('keydown', closeOnEscape))
onUnmounted(() => window.removeEventListener('keydown', closeOnEscape))
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="report-drawer-backdrop" @click.self="emit('close')">
      <aside class="report-runtime-drawer" role="dialog" aria-modal="true" aria-labelledby="runtime-drawer-title">
        <header class="report-drawer-header">
          <div>
            <p class="eyebrow">RUN METADATA</p>
            <h2 id="runtime-drawer-title">运行信息</h2>
          </div>
          <button type="button" class="drawer-close-button" aria-label="关闭运行信息" @click="emit('close')">×</button>
        </header>

        <p class="report-drawer-intro">这些字段用于审计本次报告的生成过程，不参与当前判断。</p>

        <section class="report-runtime-group">
          <h3>报告血缘</h3>
          <dl class="report-runtime-list">
            <div><dt>报告 ID</dt><dd class="mono">{{ report.report_id }}</dd></div>
            <div><dt>Dataset</dt><dd class="mono">{{ report.dataset_key }}</dd></div>
            <div><dt>冻结时间</dt><dd>{{ formatDate(report.cutoff_time) }}</dd></div>
            <div><dt>生成时间</dt><dd>{{ formatDate(String(report.created_at ?? report.cutoff_time)) }}</dd></div>
            <div><dt>Schema</dt><dd class="mono">{{ report.decision_report_schema_version ?? '—' }}</dd></div>
          </dl>
        </section>

        <section class="report-runtime-group">
          <h3>模型运行</h3>
          <dl class="report-runtime-list">
            <div><dt>Provider</dt><dd>{{ runSummary?.providers?.join(', ') || '—' }}</dd></div>
            <div><dt>Model</dt><dd>{{ runSummary?.models?.join(', ') || '—' }}</dd></div>
            <div><dt>耗时</dt><dd>{{ formatNumber(runSummary?.duration_ms ?? 0) }} ms</dd></div>
            <div><dt>Tool calls</dt><dd>{{ formatNumber(runSummary?.tool_call_count ?? 0) }}</dd></div>
            <div><dt>Tokens</dt><dd>{{ formatNumber(runSummary?.prompt_tokens ?? 0) }} prompt / {{ formatNumber(runSummary?.completion_tokens ?? 0) }} completion</dd></div>
            <div v-if="runSummary?.estimated_cost != null"><dt>估算成本</dt><dd>{{ runSummary.estimated_cost }}</dd></div>
          </dl>
        </section>

        <section class="report-runtime-group">
          <h3>运行约束</h3>
          <dl class="report-runtime-list">
            <div><dt>报告状态</dt><dd><span class="status-badge" :data-status="report.status">{{ report.status }}</span></dd></div>
            <div><dt>质量状态</dt><dd>{{ String(report.quality?.status ?? 'unknown') }}</dd></div>
            <div><dt>评分资格</dt><dd>{{ report.eligible_for_scoring === false ? '不参与评分' : '可参与评分' }}</dd></div>
            <div><dt>CTA 状态</dt><dd>{{ report.updates_official_cta_state === false ? '不更新正式状态' : '允许更新' }}</dd></div>
            <div v-if="runSummary?.skill_hashes?.length"><dt>Skill hash</dt><dd class="mono">{{ runSummary.skill_hashes.map((hash) => hash.slice(0, 8)).join(', ') }}</dd></div>
          </dl>
        </section>
      </aside>
    </div>
  </Teleport>
</template>
