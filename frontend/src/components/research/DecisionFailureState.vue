<script setup lang="ts">
defineProps<{
  status?: string
  errorMessage?: string | null
}>()
</script>

<template>
  <section class="decision-state-panel" :data-state="status ?? 'unavailable'" role="status">
    <div class="decision-state-icon" aria-hidden="true">{{ status === 'running' ? '…' : status === 'failed' || status === 'timed_out' ? '!' : '·' }}</div>
    <div>
      <p class="eyebrow">AI DECISION STATUS</p>
      <h2 v-if="status === 'running'">AI 现状分析正在运行</h2>
      <h2 v-else-if="status === 'failed' || status === 'timed_out'">AI 现状分析不可用</h2>
      <h2 v-else-if="status === 'disabled' || status === 'technical_ready'">AI Agent 当前未启用</h2>
      <h2 v-else>AI 现状分析尚未生成</h2>
      <p v-if="errorMessage" class="decision-state-message">{{ errorMessage }}</p>
      <p v-else-if="status === 'running'" class="decision-state-message">技术报告已经保留，AI 完成后刷新即可查看。</p>
      <p v-else class="decision-state-message">本次不会生成虚假的市场判断。请先查看技术报告中的冻结证据。</p>
    </div>
  </section>
</template>
