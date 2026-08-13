<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { actionLabel, evidence, list, optionFor, record, returnRange, text } from './reportHelpers'

type Ranking = Record<string, unknown>

const props = defineProps<{
  open: boolean
  symbol?: string
  rankings: Ranking[]
  optionContext: Ranking[]
  manual?: boolean
}>()

const emit = defineEmits<{
  (event: 'close'): void
  (event: 'focus-evidence', path: string): void
}>()

const activeTab = ref<'decision' | 'technical' | 'options' | 'evidence'>('decision')
const selected = computed(() => props.rankings.find((item) => String(item.symbol) === String(props.symbol)) ?? null)
const option = computed(() => optionFor(props.optionContext, props.symbol))
const pricing = computed(() => record(option.value.volatility_pricing))
const forecast = computed(() => record(selected.value?.instrument_forecast))
const selectedEvidence = computed(() => evidence(selected.value?.evidence))
const optionEvidence = computed(() => option.value.evidence_path ? [{ path: String(option.value.evidence_path), observation: '期权结构上下文' }] : [])
const allRisks = computed(() => [
  ...list(selected.value?.risks),
  ...list(selected.value?.invalidation_conditions),
  ...list(option.value.risk_flags),
])

watch(() => props.symbol, () => { activeTab.value = 'decision' })

function value(item: Ranking | null, key: string, fallback = '—'): string {
  return text(item?.[key], fallback)
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open && selected" class="report-drawer-backdrop inspection-drawer-shell">
      <aside class="instrument-detail-drawer" role="complementary" :aria-labelledby="`instrument-drawer-${selected.symbol}`">
        <header class="report-drawer-header">
          <div>
            <p class="eyebrow">INSTRUMENT DETAIL</p>
            <h2 :id="`instrument-drawer-${selected.symbol}`"><span class="mono">{{ selected.symbol }}</span></h2>
            <p class="drawer-subtitle">#{{ value(selected, 'rank') }} · {{ manual ? '关注标的' : actionLabel(selected.action) }}</p>
          </div>
          <button type="button" class="drawer-close-button" :aria-label="`关闭 ${selected.symbol} 详情`" @click="emit('close')">×</button>
        </header>

        <nav class="drawer-tabs" aria-label="标的详情标签页">
          <button v-for="tab in [{ id: 'decision', label: '判断' }, { id: 'technical', label: '技术' }, { id: 'options', label: '期权' }, { id: 'evidence', label: '风险与证据' }]" :key="tab.id" type="button" :class="{ active: activeTab === tab.id }" @click="activeTab = tab.id as 'decision' | 'technical' | 'options' | 'evidence'">
            {{ tab.label }}
          </button>
        </nav>

        <div class="drawer-content">
          <section v-if="activeTab === 'decision'" class="drawer-panel">
            <div class="drawer-fact-grid">
              <div><span>状态</span><strong>{{ manual ? '当前关注' : actionLabel(selected.action) }}</strong></div>
              <div><span>Score</span><strong>{{ value(selected, 'score') }}</strong></div>
              <div><span>置信度</span><strong>{{ value(selected, 'confidence') }}</strong></div>
              <div><span>SEPA 完整度</span><strong>{{ value(selected, 'strict_sepa_completeness') }}</strong></div>
            </div>
            <div class="drawer-copy-block"><span class="drawer-label">核心判断</span><p>{{ text(selected.thesis ?? selected.reason ?? selected.rationale, '未提供判断理由') }}</p></div>
            <div v-if="forecast.direction || forecast.probability" class="drawer-copy-block">
              <span class="drawer-label">预期路径</span>
              <p>{{ value(forecast, 'direction') }} · 概率 {{ value(forecast, 'probability') }} · 区间 {{ returnRange(forecast) }}</p>
              <small>相对 {{ value(forecast, 'relative_to') }} · {{ value(forecast, 'relative_direction') }}</small>
            </div>
            <div v-if="!manual && (selected.if_cash || selected.if_held)" class="drawer-scenario-list">
              <div v-if="selected.if_cash" class="drawer-scenario" data-scenario="cash"><span>当前为空仓</span><strong>{{ actionLabel(record(selected.if_cash).action) }}</strong><p>{{ text(record(selected.if_cash).reason) }}</p></div>
              <div v-if="selected.if_held" class="drawer-scenario" data-scenario="held"><span>已经持有</span><strong>{{ actionLabel(record(selected.if_held).action) }}</strong><p>{{ text(record(selected.if_held).reason) }}</p></div>
            </div>
          </section>

          <section v-else-if="activeTab === 'technical'" class="drawer-panel">
            <div v-if="Object.keys(record(selected.technical)).length" class="drawer-fact-grid">
              <div v-for="(item, key) in record(selected.technical)" :key="String(key)"><span>{{ key }}</span><strong>{{ text(item) }}</strong></div>
            </div>
            <div v-else class="drawer-empty"><strong>技术详情尚未单独投影</strong><span>阶段 B 会把价格、均线、MACD、相对强弱和量价图表接入这里。</span></div>
          </section>

          <section v-else-if="activeTab === 'options'" class="drawer-panel">
            <div v-if="Object.keys(option).length" class="drawer-fact-grid drawer-fact-grid-2">
              <div><span>IV / HV30</span><strong>{{ text(pricing.iv) }} / {{ text(pricing.hv_30d) }}</strong></div>
              <div><span>IV−HV</span><strong>{{ text(pricing.iv_hv_spread) }}</strong></div>
              <div><span>波动率定价</span><strong>{{ text(pricing.iv_hv_regime) }}</strong></div>
              <div><span>Gamma 状态</span><strong>{{ text(option.gamma_regime) }}</strong></div>
              <div><span>Gamma Flip</span><strong>{{ text(option.primary_gamma_flip) }}</strong></div>
              <div><span>Max Pain</span><strong>{{ text(option.max_pain) }}</strong></div>
              <div><span>Put / Call 墙</span><strong>{{ text(option.put_wall) }} / {{ text(option.call_wall) }}</strong></div>
              <div><span>Expected Move</span><strong>{{ text(record(option.expected_move).amount) }}</strong></div>
            </div>
            <div v-else class="drawer-empty"><strong>暂无期权结构</strong><span>该标的没有进入当前冻结证据包的期权上下文。</span></div>
          </section>

          <section v-else class="drawer-panel">
            <div v-if="allRisks.length" class="drawer-list-block"><span class="drawer-label">风险与失效条件</span><ul><li v-for="risk in allRisks" :key="risk">{{ risk }}</li></ul></div>
            <div v-if="list(selected?.missing_fields).length" class="drawer-list-block"><span class="drawer-label">缺失字段</span><ul><li v-for="field in list(selected?.missing_fields)" :key="field">{{ field }}</li></ul></div>
            <div v-if="selectedEvidence.length || optionEvidence.length" class="drawer-list-block"><span class="drawer-label">证据入口</span><div class="drawer-evidence-list"><button v-for="item in [...selectedEvidence, ...optionEvidence]" :key="item.path" type="button" class="evidence-link" @click="emit('focus-evidence', item.path)">{{ item.observation || '打开技术证据' }}</button></div></div>
            <div v-if="!allRisks.length && !selectedEvidence.length && !optionEvidence.length" class="drawer-empty"><strong>没有额外风险或证据引用</strong><span>技术报告仍保留本次冻结数据的完整质量说明。</span></div>
          </section>
        </div>
      </aside>
    </div>
  </Teleport>
</template>
