<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { TechnicalReport } from '@/types/research'
import { actionLabel, evidence, list, optionFor, record, returnRange, text } from './reportHelpers'

type Ranking = Record<string, unknown>

const props = defineProps<{
  open: boolean
  symbol?: string
  rankings: Ranking[]
  optionContext: Ranking[]
  technical?: TechnicalReport | null
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
const technicalCards = computed(() => {
  const themes = record(record(props.technical).instruments).themes as Record<string, unknown>
  return Object.values(themes).flatMap((value) => Array.isArray(value) ? value.filter((item): item is Ranking => Boolean(item && typeof item === 'object')) : [])
})
const technicalCard = computed(() => technicalCards.value.find((item) => String(item.symbol) === String(props.symbol)) ?? null)
const technicalMetrics = computed(() => record(technicalCard.value?.technical))
const technicalQuote = computed(() => record(technicalCard.value?.quote))
const technicalRelativeStrength = computed(() => record(technicalCard.value?.relative_strength))
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

function metric(valueToFormat: unknown, fallback = '—'): string {
  const item = record(valueToFormat)
  return text(item.value ?? valueToFormat, fallback)
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
            <template v-if="technicalCard">
              <div class="drawer-fact-grid drawer-fact-grid-2">
                <div><span>价格</span><strong>{{ text(technicalQuote.last_price ?? technicalQuote.regular_price) }}</strong></div>
                <div><span>日变动</span><strong>{{ text(technicalQuote.change_percent) }}%</strong></div>
                <div><span>趋势</span><strong>{{ text(technicalCard.trend) }}</strong></div>
                <div><span>技术截至</span><strong>{{ text(technicalMetrics.as_of) }}</strong></div>
              </div>
              <div class="drawer-technical-section">
                <span class="drawer-label">收益与均线</span>
                <div class="drawer-fact-grid drawer-fact-grid-2">
                  <div><span>收益 1D / 5D / 20D</span><strong>{{ text(record(technicalMetrics.returns_percent)['1d']) }} / {{ text(record(technicalMetrics.returns_percent)['5d']) }} / {{ text(record(technicalMetrics.returns_percent)['20d']) }}</strong></div>
                  <div><span>收益 60D / 252D</span><strong>{{ text(record(technicalMetrics.returns_percent)['60d']) }} / {{ text(record(technicalMetrics.returns_percent)['252d']) }}</strong></div>
                  <div><span>MA20 / MA50</span><strong>{{ text(record(technicalMetrics.moving_average)['20d']) }} / {{ text(record(technicalMetrics.moving_average)['50d']) }}</strong></div>
                  <div><span>MA100 / MA200</span><strong>{{ text(record(technicalMetrics.moving_average)['100d']) }} / {{ text(record(technicalMetrics.moving_average)['200d']) }}</strong></div>
                </div>
              </div>
              <div class="drawer-technical-section">
                <span class="drawer-label">动量、波动与量价</span>
                <div class="drawer-fact-grid drawer-fact-grid-2">
                  <div><span>RSI14 · Wilder</span><strong>{{ metric(technicalMetrics.rsi14) }} · {{ text(record(technicalMetrics.rsi14).state) }}</strong></div>
                  <div><span>MACD DIF / DEA / Hist</span><strong>{{ text(record(technicalMetrics.macd_12_26_9).dif) }} / {{ text(record(technicalMetrics.macd_12_26_9).dea) }} / {{ text(record(technicalMetrics.macd_12_26_9).histogram) }}</strong></div>
                  <div><span>RV20 / ATR14%</span><strong>{{ metric(record(technicalMetrics.realized_volatility)['20d']) }} / {{ metric(technicalMetrics.atr14_percent) }}%</strong></div>
                  <div><span>量比 / Effort</span><strong>{{ text(record(technicalMetrics.volume_effort_result).volume_ratio_20d) }}× / {{ text(record(technicalMetrics.volume_effort_result).signal) }}</strong></div>
                </div>
              </div>
              <div class="drawer-technical-section">
                <span class="drawer-label">相对强弱与位置</span>
                <div class="drawer-fact-grid drawer-fact-grid-2">
                  <div><span>超额收益 vs QQQ · 5D / 20D / 60D</span><strong>{{ text(record(technicalRelativeStrength.excess_returns_percent)['5d']) }} / {{ text(record(technicalRelativeStrength.excess_returns_percent)['20d']) }} / {{ text(record(technicalRelativeStrength.excess_returns_percent)['60d']) }}</strong></div>
                  <div><span>距 252D 高 / 低</span><strong>{{ text(record(technicalMetrics.high_low_distance_percent)['252d_high']) }} / {{ text(record(technicalMetrics.high_low_distance_percent)['252d_low']) }}</strong></div>
                </div>
              </div>
            </template>
            <div v-else class="drawer-empty"><strong>暂无该标的技术证据</strong><span>当前报告的技术整理数据中没有找到 {{ selected.symbol }}，请查看技术报告的个股技术标签。</span></div>
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
