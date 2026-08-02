<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterLink } from 'vue-router'

import AppShell from '@/components/AppShell.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { useUrusStore } from '@/stores/urus'
import { formatDate, runTypeLabel } from '@/utils/format'

const store = useUrusStore()

onMounted(() => {
  void store.loadRuns()
})
</script>

<template>
  <AppShell />
  <main class="page-shell narrow-page">
    <section class="page-intro">
      <p class="eyebrow">RUN HISTORY</p>
      <h1>运行记录</h1>
    </section>
    <div v-if="store.error" class="error-banner" role="alert">{{ store.error }}</div>
    <section v-if="store.runs.length" class="runs-list">
      <RouterLink v-for="run in store.runs" :key="run.id" class="run-list-row" :to="`/runs/${run.id}`">
        <div>
          <span class="eyebrow">{{ runTypeLabel(run.run_type) }}</span>
          <strong class="mono">{{ run.id }}</strong>
          <small>{{ formatDate(run.cutoff_time) }}</small>
        </div>
        <div class="run-row-meta"><StatusBadge :status="run.status" /></div>
      </RouterLink>
    </section>
    <section v-else class="empty-panel">
      <p class="eyebrow">EMPTY</p>
      <h2>还没有运行记录。</h2>
      <p>回到 Dashboard 启动一次框架 mock 运行。</p>
    </section>
  </main>
</template>
