<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import AppShell from '@/components/AppShell.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import StepTimeline from '@/components/StepTimeline.vue'
import { useUrusStore } from '@/stores/urus'
import { formatDate, runTypeLabel } from '@/utils/format'

const route = useRoute()
const store = useUrusStore()
const runId = computed(() => String(route.params.runId ?? ''))

async function load() {
  if (runId.value) await store.loadRun(runId.value)
}

onMounted(() => void load())
watch(runId, () => void load())
</script>

<template>
  <AppShell />
  <main class="page-shell narrow-page">
    <RouterLink class="back-link" to="/runs">← 返回运行列表</RouterLink>
    <div v-if="store.error" class="error-banner" role="alert">{{ store.error }}</div>
    <template v-if="store.selectedRun">
      <section class="page-intro detail-intro">
        <div>
          <p class="eyebrow">{{ runTypeLabel(store.selectedRun.run_type) }} / RUN DETAIL</p>
          <h1>运行详情</h1>
          <p class="mono detail-id">{{ store.selectedRun.id }}</p>
        </div>
        <div class="heading-meta"><StatusBadge :status="store.selectedRun.status" /></div>
      </section>
      <section class="detail-meta">
        <div><span>截止时间</span><strong>{{ formatDate(store.selectedRun.cutoff_time) }}</strong></div>
        <div><span>完成时间</span><strong>{{ formatDate(store.selectedRun.completed_at) }}</strong></div>
        <div><span>snapshot</span><strong class="mono">{{ store.selectedRun.snapshot_id || '不可用' }}</strong></div>
      </section>
      <section class="section-block detail-section">
        <div class="section-heading compact-heading"><div><p class="eyebrow">WORKFLOW</p><h2>步骤状态</h2></div></div>
        <StepTimeline :steps="store.selectedRun.steps" />
      </section>
      <section v-if="store.selectedReadModel" class="section-block detail-section">
        <div class="section-heading compact-heading"><div><p class="eyebrow">SNAPSHOT / READ MODEL</p><h2>冻结输出</h2></div><StatusBadge :status="store.selectedReadModel.data_quality.status" /></div>
        <div class="snapshot-summary">
          <p>{{ store.selectedReadModel.data_quality.message }}</p>
          <div class="snapshot-tags"><span>schema {{ store.selectedReadModel.schema_version }}</span><span>quality {{ store.selectedReadModel.data_quality.status }}</span><span>{{ store.selectedReadModel.run_status }}</span></div>
        </div>
        <details class="raw-preview">
          <summary>查看 read model JSON</summary>
          <pre>{{ JSON.stringify(store.selectedReadModel, null, 2) }}</pre>
        </details>
      </section>
    </template>
    <section v-else-if="!store.error" class="empty-panel"><p>正在读取运行详情…</p></section>
  </main>
</template>
