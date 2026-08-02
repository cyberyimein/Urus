<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import AppShell from '@/components/AppShell.vue'
import MockBadge from '@/components/MockBadge.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import StepTimeline from '@/components/StepTimeline.vue'
import { useUrusStore } from '@/stores/urus'
import type { RunType } from '@/types/api'
import { formatDate, formatNumber, runTypeLabel } from '@/utils/format'

const store = useUrusStore()
const runType = ref<RunType>('pre_market')
const simulateMacroEvent = ref(false)
const simulateInstrumentEvent = ref(false)
const readModel = computed(() => store.latestReadModel)

async function triggerRun() {
  await store.triggerRun(runType.value, {
    simulateMacroEvent: simulateMacroEvent.value,
    simulateInstrumentEvent: simulateInstrumentEvent.value,
  })
}

onMounted(() => {
  void store.loadDashboard()
})
</script>

<template>
  <AppShell />
  <main class="page-shell">
    <section class="hero">
      <div>
        <p class="eyebrow">URUS / WORKFLOW FRAMEWORK</p>
        <h1>把研究流程，<em>先跑起来。</em></h1>
        <p class="hero-copy">这是离线可运行的框架基线。七个步骤共享一次运行上下文，并将结果冻结成可读取的 snapshot。</p>
      </div>
      <div class="run-launcher">
        <label class="field-label" for="run-type">运行类型</label>
        <select id="run-type" v-model="runType">
          <option value="pre_market">盘前</option>
          <option value="pre_close">收盘前一小时</option>
        </select>
        <label class="check-row"><input v-model="simulateMacroEvent" type="checkbox" /><span>模拟宏观事件</span></label>
        <label class="check-row"><input v-model="simulateInstrumentEvent" type="checkbox" /><span>模拟个股事件</span></label>
        <button class="primary-button" :disabled="store.busy" @click="triggerRun">{{ store.busy ? '运行中…' : '开始运行' }}</button>
      </div>
    </section>

    <div v-if="store.error" class="error-banner" role="alert">{{ store.error }}</div>

    <template v-if="store.latestRun && readModel">
      <section class="connection-strip">
        <div>
          <span class="eyebrow">后端连接</span>
          <strong :data-connection="store.connection">{{ store.connection === 'connected' ? '已连接' : '不可用' }}</strong>
        </div>
        <div class="connection-meta"><span>{{ runTypeLabel(store.latestRun.run_type) }}</span><span class="mono">run {{ store.latestRun.id.slice(0, 8) }}</span></div>
      </section>

      <section class="run-meta-grid section-block">
        <div><span>运行状态</span><strong><StatusBadge :status="store.latestRun.status" /></strong></div>
        <div><span>截止时间</span><strong>{{ formatDate(store.latestRun.cutoff_time) }}</strong></div>
        <div><span>生成时间</span><strong>{{ formatDate(readModel.generated_at) }}</strong></div>
        <div><span>数据质量</span><strong><StatusBadge :status="readModel.data_quality.status" /></strong></div>
      </section>

      <section class="card-grid section-block">
        <article v-if="readModel.market" class="data-card">
          <div class="card-heading"><div><p class="eyebrow">1A / MARKET</p><h3>{{ readModel.market.symbol }}</h3></div><MockBadge /></div>
          <div class="ticker-line"><strong>{{ formatNumber(readModel.market.last_price) }}</strong><span>{{ readModel.market.label }}</span></div>
          <div class="metric-row"><span>变化</span><strong>{{ formatNumber(readModel.market.change_percent) }}%</strong></div>
          <p class="card-note">{{ readModel.market.note }}</p>
        </article>
        <article v-if="readModel.instrument" class="data-card">
          <div class="card-heading"><div><p class="eyebrow">3A / INSTRUMENT</p><h3>{{ readModel.instrument.symbol }}</h3></div><MockBadge /></div>
          <div class="ticker-line"><strong>{{ formatNumber(readModel.instrument.last_price) }}</strong><span>{{ readModel.instrument.label }}</span></div>
          <div class="metric-row"><span>变化</span><strong>{{ formatNumber(readModel.instrument.change_percent) }}%</strong></div>
          <p class="card-note">{{ readModel.instrument.note }}</p>
        </article>
        <article class="data-card text-card">
          <div class="card-heading"><div><p class="eyebrow">2 / OPTIONS</p><h3>期权模块</h3></div><StatusBadge :status="readModel.options.status" /></div>
          <p class="large-note">{{ readModel.options.note }}</p>
        </article>
        <article class="data-card text-card">
          <div class="card-heading"><div><p class="eyebrow">4 / DECISION</p><h3>决策模块</h3></div><StatusBadge :status="readModel.decision.status" /></div>
          <p class="large-note">{{ readModel.decision.summary }}</p>
        </article>
      </section>

      <section class="section-block">
        <div class="section-heading"><div><p class="eyebrow">CONDITIONAL STEPS</p><h2>事件摘要</h2></div><MockBadge /></div>
        <div class="card-grid">
          <article class="data-card text-card"><div class="card-heading"><h3>宏观事件</h3><StatusBadge :status="readModel.macro_event.status" /></div><p class="large-note">{{ readModel.macro_event.summary || readModel.macro_event.reason }}</p></article>
          <article class="data-card text-card"><div class="card-heading"><h3>个股事件</h3><StatusBadge :status="readModel.instrument_event.status" /></div><p class="large-note">{{ readModel.instrument_event.summary || readModel.instrument_event.reason }}</p></article>
        </div>
      </section>

      <section class="section-block">
        <div class="section-heading"><div><p class="eyebrow">WORKFLOW</p><h2>步骤状态</h2></div></div>
        <StepTimeline :steps="store.latestRun.steps" />
      </section>

      <section class="section-block quality-card data-card">
        <div class="section-heading compact-heading"><div><p class="eyebrow">SNAPSHOT QUALITY</p><h2>数据质量</h2></div><StatusBadge :status="readModel.data_quality.status" /></div>
        <p class="large-note">{{ readModel.data_quality.message }}</p>
        <p v-for="warning in readModel.data_quality.warnings" :key="warning" class="card-note">{{ warning }}</p>
        <p v-for="error in readModel.data_quality.errors" :key="error" class="missing-note">{{ error }}</p>
      </section>
    </template>

    <section v-else-if="!store.error" class="empty-panel"><p class="eyebrow">NO RUN</p><h2>还没有运行记录。</h2><p>启动一次框架运行后，这里会展示 snapshot 和步骤状态。</p></section>
  </main>
</template>
