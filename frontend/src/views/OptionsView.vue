<script setup lang="ts">
import { onMounted, ref } from 'vue'

import AppShell from '@/components/AppShell.vue'
import OptionsPanel from '@/components/OptionsPanel.vue'
import { useUrusStore } from '@/stores/urus'
import type { RunType } from '@/types/api'
import { formatDate, runTypeLabel } from '@/utils/format'

const store = useUrusStore()
const runType = ref<RunType>('pre_market')

async function triggerRun() {
  await store.triggerRun(runType.value, {
    simulateMacroEvent: false,
    simulateInstrumentEvent: false,
  })
}

onMounted(() => void store.loadDashboard())
</script>

<template>
  <AppShell />
  <main class="page-shell validation-page options-page">
    <header class="validation-header">
      <div><p class="eyebrow">STAGE 2 / OPTIONS VALIDATION</p><h1>期权结构验证</h1></div>
      <div class="run-launcher compact-launcher">
        <label class="field-label" for="options-run-type">运行类型</label>
        <select id="options-run-type" v-model="runType"><option value="pre_market">盘前</option><option value="pre_close">收盘前一小时</option></select>
        <button class="primary-button" :disabled="store.busy" @click="triggerRun">{{ store.busy ? '采集中…' : '采集期权快照' }}</button>
      </div>
    </header>

    <div v-if="store.error" class="error-banner" role="alert">{{ store.error }}</div>

    <section v-if="store.latestRun && store.latestReadModel" class="validation-workspace">
      <div class="connection-strip"><div><span class="eyebrow">后端连接</span><strong :data-connection="store.connection">{{ store.connection === 'connected' ? '已连接' : '不可用' }}</strong></div><div class="connection-meta"><span>{{ runTypeLabel(store.latestRun.run_type) }}</span><span class="mono">run {{ store.latestRun.id.slice(0, 8) }}</span><span>{{ formatDate(store.latestReadModel.generated_at) }}</span></div></div>
      <OptionsPanel :options="store.latestReadModel.options" />
    </section>

    <section v-else-if="!store.error" class="empty-panel"><p class="eyebrow">NO OPTIONS SNAPSHOT</p><h2>还没有期权结构数据。</h2><p>点击“采集期权快照”，仅使用 QQQ 和 INTC 完成 Stage 2 开发验证。</p></section>
  </main>
</template>
