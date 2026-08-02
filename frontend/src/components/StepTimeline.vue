<script setup lang="ts">
import type { StepCode, StepRun, ReadModelStep } from '@/types/api'
import { formatDate } from '@/utils/format'
import StatusBadge from './StatusBadge.vue'

const props = defineProps<{ steps: Array<StepRun | ReadModelStep> }>()

const labels: Record<StepCode, string> = {
  '1a': '1A · 大盘采集',
  '1b': '1B · 宏观事件摘要',
  '2': '2 · 期权结构',
  '3a': '3A · 个股采集',
  '3b': '3B · 个股事件摘要',
  '4': '4 · 决策占位',
  '5': '5 · 输出 read model',
}

function code(step: StepRun | ReadModelStep): StepCode {
  return 'step_code' in step ? step.step_code : step.code
}

function startedAt(step: StepRun | ReadModelStep): string | null {
  return 'started_at' in step ? step.started_at : null
}
</script>

<template>
  <div v-if="props.steps.length" class="step-timeline">
    <article v-for="step in props.steps" :key="code(step)" class="step-row" :data-status="step.status">
      <div class="step-marker">{{ code(step).toUpperCase() }}</div>
      <div class="step-copy">
        <div class="step-titleline">
          <strong>{{ labels[code(step)] }}</strong>
          <StatusBadge :status="step.status" />
        </div>
        <p>{{ step.summary || '没有补充说明。' }}</p>
        <small v-if="step.error_message" class="step-error">{{ step.error_message }}</small>
        <small v-else-if="startedAt(step)" class="step-time">{{ formatDate(startedAt(step)) }}</small>
      </div>
    </article>
  </div>
  <p v-else class="empty-state compact">还没有运行记录。</p>
</template>
