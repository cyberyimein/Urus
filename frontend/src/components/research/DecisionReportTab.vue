<script setup lang="ts">
import { computed } from 'vue'

import type { DecisionReport } from '@/types/research'
import { formatDate } from '@/utils/format'
import AttentionTable from './AttentionTable.vue'
import DecisionFailureState from './DecisionFailureState.vue'
import DecisionSupportMatrix from './DecisionSupportMatrix.vue'
import DecisionSummaryHero from './DecisionSummaryHero.vue'
import InstrumentDetailDrawer from './InstrumentDetailDrawer.vue'
import { list, record, records, text } from './reportHelpers'

const props = defineProps<{
  report: DecisionReport | null
  status?: string
  errorMessage?: string | null
  selectedSymbol?: string
}>()

const emit = defineEmits<{
  (event: 'focus-evidence', path: string): void
  (event: 'select-symbol', symbol: string): void
  (event: 'close-symbol'): void
}>()

const rankings = computed(() => records(props.report?.rankings))
const optionContext = computed(() => records(props.report?.equity_option_context))
const forecast = computed(() => record(props.report?.forecast))
const review = computed(() => record(props.report?.review))
const phaseEvaluations = computed(() => records(record(props.report?.objective_evaluation).phase_evaluations))
const isManual = computed(() => props.report?.decision_phase === 'current_state' || props.report?.analysis_mode === 'current_state' || props.report?.trigger_type === 'manual')
const isReview = computed(() => props.report?.decision_phase === 'post_close_review')
const hasForecast = computed(() => !isManual.value && Object.keys(forecast.value).length > 0 && !isReview.value)
const hasReview = computed(() => isReview.value && Object.keys(review.value).length > 0)
const showFailureState = computed(() => Boolean(props.report && ['failed', 'timed_out'].includes(String(props.report.status)) && rankings.value.length === 0 && Object.keys(record(props.report.market_regime)).length === 0))

function listText(value: unknown): string {
  return list(value).join(' · ') || '暂无'
}
</script>

<template>
  <DecisionFailureState
    v-if="!report || showFailureState"
    :status="status ?? report?.status"
    :error-message="errorMessage || (report ? text(report.error_message ?? record(report.market_analysis).error_message, 'AI 决策没有生成可用输出。') : null)"
  />

  <template v-else>
    <div class="report-toolbar report-toolbar-compact">
      <span class="status-badge" :data-status="report.status">AI {{ report.status }}</span>
      <span class="live-badge">{{ isManual ? '手动 · 当前状态' : isReview ? '正式 · 收盘复盘' : '正式 · 盘前判断' }}</span>
      <span v-if="report.agent_profile" class="subtle">{{ report.agent_profile }}</span>
      <span class="subtle">结构化输出 · 程序组装</span>
    </div>

    <DecisionSummaryHero :report="report" :rankings="rankings" :option-context="optionContext" />

    <DecisionSupportMatrix :report="report" @focus-evidence="emit('focus-evidence', $event)" />

    <AttentionTable
      :rankings="rankings"
      :option-context="optionContext"
      :manual="isManual"
      :selected-symbol="selectedSymbol"
      @select-symbol="emit('select-symbol', $event)"
    />

    <section v-if="hasForecast || hasReview" class="decision-context-section">
      <details class="report-disclosure">
        <summary>{{ hasReview ? '查看复盘与预测对照' : '查看预测路径与条件' }}</summary>

        <div v-if="hasForecast" class="decision-disclosure-content">
          <div class="decision-disclosure-header">
            <div><p class="eyebrow">PHASE FORECAST</p><h3>{{ text(report.forecast_horizon, '当前阶段') }}</h3></div>
            <span class="mono">{{ text(forecast.direction) }} · {{ text(forecast.confidence) }}</span>
          </div>
          <p class="report-note report-thesis">{{ text(forecast.expected_path, '未提供预期路径') }}</p>
          <div class="decision-condition-grid">
            <div><span>确认条件</span><p>{{ listText(forecast.confirmation_conditions) }}</p></div>
            <div><span>失效条件</span><p>{{ listText(forecast.invalidation_conditions) }}</p></div>
            <div><span>催化与风险</span><p>{{ listText(forecast.catalysts) }}</p></div>
          </div>
          <div v-if="Array.isArray(forecast.scenarios) && forecast.scenarios.length" class="decision-scenario-list">
            <div v-for="scenario in records(forecast.scenarios)" :key="String(scenario.label)" class="decision-scenario"><strong>{{ text(scenario.label) }}</strong><span>{{ text(scenario.probability) }}</span><p>{{ text(scenario.direction) }} · {{ listText(scenario.conditions) }}</p></div>
          </div>
        </div>

        <div v-else-if="hasReview" class="decision-disclosure-content">
          <div class="decision-disclosure-header">
            <div><p class="eyebrow">DAILY REVIEW</p><h3>当日行情与预测复盘</h3></div>
            <span class="mono">{{ text(report.trading_date) }}</span>
          </div>
          <p class="report-note report-thesis">{{ text(review.session_summary, '未提供复盘摘要') }}</p>
          <p class="report-note">{{ text(review.market_outcome, '未提供市场结果') }}</p>
          <div v-if="Object.keys(record(review.pre_market_evaluation)).length" class="decision-review-fact"><span>盘前预测</span><strong>{{ text(record(review.pre_market_evaluation).verdict) }}</strong><p>{{ text(record(review.pre_market_evaluation).explanation) }}</p></div>
          <div v-if="phaseEvaluations.length" class="decision-review-fact"><span>程序评估</span><strong>{{ phaseEvaluations.length }} 个阶段</strong><p>{{ phaseEvaluations.map((item) => `${text(item.phase)} · ${text(item.verdict)}`).join('；') }}</p></div>
        </div>
      </details>
    </section>

    <details v-if="report.portfolio_warnings?.length" class="report-disclosure report-disclosure-warning">
      <summary>查看组合级风险（{{ report.portfolio_warnings.length }}）</summary>
      <ul class="disclosure-list"><li v-for="warning in report.portfolio_warnings" :key="warning">{{ warning }}</li></ul>
    </details>

    <p class="research-disclaimer">{{ report.disclaimer ?? 'Research output only; no order was placed.' }} · 生成于 {{ formatDate(String(report.generated_at ?? '')) }}</p>

    <InstrumentDetailDrawer
      :open="Boolean(selectedSymbol)"
      :symbol="selectedSymbol"
      :rankings="rankings"
      :option-context="optionContext"
      :manual="isManual"
      @close="emit('close-symbol')"
      @focus-evidence="emit('focus-evidence', $event)"
    />
  </template>
</template>
