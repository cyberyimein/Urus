<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ResearchReportIndex, ResearchReportPayload } from '@/types/research'
import { formatDate } from '@/utils/format'
import ReportRuntimeDrawer from '@/components/research/ReportRuntimeDrawer.vue'

const props = defineProps<{
  report: ResearchReportPayload
  available: ResearchReportIndex[]
  activeTab: 'technical' | 'decision' | 'trace'
}>()

const isManual = computed(() => props.report.trigger_type === 'manual' || props.report.decision_phase === 'current_state')
const reportTitle = computed(() => isManual.value
  ? `手动 · ${props.report.session_context === 'pre_market' ? '盘前' : props.report.session_context === 'intraday' ? '盘中' : '即时'}分析`
  : props.report.decision_phase === 'post_close_review' ? '正式 · 收盘复盘' : '正式 · 盘前决策')
const runtimeOpen = ref(false)

const emit = defineEmits<{
  (event: 'select-tab', tab: 'technical' | 'decision' | 'trace'): void
  (event: 'select-report', reportId: string): void
}>()
</script>

<template>
  <section class="research-header">
    <div>
      <p class="eyebrow">{{ isManual ? 'ON-DEMAND / CURRENT STATE' : 'OFFICIAL DAILY CYCLE' }}</p>
      <h1>{{ reportTitle }}</h1>
      <p class="research-subtitle">冻结于 {{ formatDate(report.cutoff_time) }} · {{ report.dataset_key }}</p>
    </div>
    <div class="research-header-meta">
      <span class="status-badge" :data-status="report.status">{{ report.status }}</span>
      <span class="quality-status">质量：{{ String(report.quality?.status ?? 'unknown') }}</span>
      <span v-if="available.some((item) => item.report_id !== report.report_id && ['failed', 'timed_out', 'running'].includes(item.status))" class="notice-inline">存在未成功的更新尝试</span>
      <button type="button" class="runtime-info-button" @click="runtimeOpen = true">运行信息</button>
      <label v-if="available.length > 1" class="report-version-select">
        <span>报告版本</span>
        <select :value="report.report_id" @change="emit('select-report', ($event.target as HTMLSelectElement).value)">
          <option v-for="item in available" :key="item.report_id" :value="item.report_id">
            {{ formatDate(item.created_at ?? item.cutoff_time) }} · {{ item.status }} · {{ item.report_id.slice(0, 8) }}
          </option>
        </select>
      </label>
    </div>
  </section>
  <p class="research-header-disclaimer">{{ String(report.quality?.message ?? '研究输出仅供研究，不构成投资建议或交易指令。') }}</p>
  <nav class="research-tabs" aria-label="研究报告标签页">
    <button
      v-for="item in [
        { id: 'technical', label: '技术整理报告', hint: '程序生成' },
        { id: 'decision', label: isManual ? 'AI 现状分析' : 'AI 决策报告', hint: '结构化输出' },
        { id: 'trace', label: isManual ? 'AI 工作流验证' : 'AI 决策复盘', hint: '节点轨迹' },
      ]"
      :key="item.id"
      class="research-tab"
      :class="{ active: activeTab === item.id }"
      type="button"
      @click="emit('select-tab', item.id as 'technical' | 'decision' | 'trace')"
    >
      <strong>{{ item.label }}</strong>
      <small>{{ item.hint }}</small>
    </button>
  </nav>
  <ReportRuntimeDrawer :report="report" :open="runtimeOpen" @close="runtimeOpen = false" />
</template>
