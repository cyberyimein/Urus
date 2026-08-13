<script setup lang="ts">
import { computed } from 'vue'

import type { DecisionReport } from '@/types/research'
import { directionLabel, list, number, record, records, text } from './reportHelpers'

const props = defineProps<{
  report: DecisionReport
  rankings: Record<string, unknown>[]
  optionContext: Record<string, unknown>[]
}>()

const regime = computed(() => record(props.report.market_regime))
const forecast = computed(() => record(props.report.forecast))
const review = computed(() => record(props.report.review))
const marketAnalysis = computed(() => record(props.report.market_analysis))
const isManual = computed(() => props.report.decision_phase === 'current_state' || props.report.analysis_mode === 'current_state')

const marketState = computed(() => text(
  regime.value.classification ?? regime.value.direction ?? forecast.value.direction,
  '状态未声明',
))

const ctaState = computed(() => text(
  regime.value.cta_state
    ?? regime.value.cta
    ?? regime.value.systematic_flow_state
    ?? marketAnalysis.value.cta_state
    ?? marketAnalysis.value.cta,
  '未提供 CTA 摘要',
))

const volatilityState = computed(() => {
  const contexts = props.optionContext
    .map((item) => ({ symbol: text(item.symbol), pricing: record(item.volatility_pricing) }))
    .filter((item) => Object.keys(item.pricing).length)
  const first = contexts[0]
  if (!first) return '未提供期权定价'
  const regimeLabel = text(first.pricing.iv_hv_regime, 'IV/HV 未知')
  return contexts.length > 1 ? `${regimeLabel} · ${contexts.length} 个标的` : `${first.symbol} · ${regimeLabel}`
})

const qualityState = computed(() => text(props.report.quality?.status, '质量未知'))

const summary = computed(() => text(
  regime.value.summary
    ?? regime.value.thesis
    ?? forecast.value.expected_path
    ?? review.value.session_summary
    ?? review.value.market_outcome
    ?? marketAnalysis.value.summary,
  isManual.value ? '当前状态没有生成文字摘要，请从下方证据查看。' : '本次报告没有生成市场摘要。',
))

const riskCount = computed(() => props.rankings.reduce((count, item) => count + list(item.risks).length + list(item.invalidation_conditions).length, 0))
const missingCount = computed(() => props.rankings.reduce((count, item) => count + list(item.missing_fields).length, 0) + list(props.report.quality?.missing_fields).length)
const evidenceCount = computed(() => records(regime.value.evidence).length)
const confidence = computed(() => {
  const raw = regime.value.confidence ?? forecast.value.confidence
  const parsed = number(raw)
  return parsed !== null && Math.abs(parsed) <= 1 ? `${(parsed * 100).toFixed(0)}%` : text(raw, '未提供')
})
const direction = computed(() => directionLabel(forecast.value.direction ?? regime.value.direction ?? regime.value.classification))
</script>

<template>
  <section class="decision-summary-hero">
    <div class="decision-summary-heading">
      <div>
        <p class="eyebrow">{{ isManual ? 'CURRENT STATE' : 'AI DECISION' }}</p>
        <h2>{{ isManual ? '当前市场状态' : '当前市场判断' }}</h2>
      </div>
      <span class="decision-confidence">置信度 {{ confidence }}</span>
    </div>

    <div class="decision-state-grid">
      <div class="decision-state-metric"><span>市场</span><strong>{{ marketState }}</strong><small>{{ direction }}</small></div>
      <div class="decision-state-metric"><span>CTA 边际</span><strong>{{ ctaState }}</strong><small>来自本次 AI 输出</small></div>
      <div class="decision-state-metric"><span>波动率定价</span><strong>{{ volatilityState }}</strong><small>IV / HV30 过滤</small></div>
      <div class="decision-state-metric"><span>数据质量</span><strong>{{ qualityState }}</strong><small>冻结证据状态</small></div>
    </div>

    <p class="decision-summary-copy">{{ summary }}</p>

    <div class="decision-summary-counts" aria-label="报告摘要统计">
      <span><strong>{{ rankings.length }}</strong> 个关注标的</span>
      <span><strong>{{ riskCount }}</strong> 个风险条件</span>
      <span><strong>{{ missingCount }}</strong> 个数据缺口</span>
      <span><strong>{{ evidenceCount }}</strong> 条市场证据</span>
    </div>
  </section>
</template>
