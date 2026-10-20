<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { getSystematicRiskOverview } from '@/api/systematicRisk'
import type {
  SystematicRiskModel,
  SystematicRiskOverview,
  SystematicRiskSymbol,
} from '@/types/systematicRisk'

const props = defineProps<{ refreshToken: number }>()

const overview = ref<SystematicRiskOverview | null>(null)
const loading = ref(true)
const error = ref('')
let controller: AbortController | null = null
let generation = 0

const statusLabels: Record<string, string> = {
  disabled: '已关闭',
  ok: '可用',
  partial: '部分模型可用',
  unavailable: '暂不可用',
  error: '读取错误',
}

const directionLabels: Record<string, string> = {
  long_reduction: '多头减仓',
  short_cover: '空头回补',
  long_to_short: '由多转空',
  short_to_long: '由空转多',
  long_increase: '模型多头增加',
  short_increase: '模型空头增加',
  unchanged: '不变',
  unavailable: '不可用',
}

const flipPriceReasons: Record<string, string> = {
  insufficient_flip_history: '翻转价历史不足',
  non_positive_flip_root: '解析根不是正价',
  flip_root_verification_failed: '解析根未通过代入复核',
}

const symbols = ['SPY', 'QQQ', 'IWM']

function statusLabel(status: string): string {
  return statusLabels[status] ?? status
}

function formatPrice(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : '—'
}

function formatPercent(value: number | null | undefined, digits = 2): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${(value * 100).toFixed(digits)}%`
    : '—'
}

function signedPercentagePoints(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${value > 0 ? '+' : ''}${(value * 100).toFixed(1)} pp`
    : '—'
}

function trendLabel(value: number | null | undefined): string {
  if (value === 1) return '多头趋势'
  if (value === -1) return '空头趋势'
  if (value === 0) return '中性趋势'
  return '不可用'
}

function modelLabel(model: SystematicRiskModel): string {
  const [fast, slow] = model.fast_slow
  return `SMA ${fast}/${slow}`
}

function directionLabel(model: SystematicRiskModel): string {
  return directionLabels[model.exposure_change.direction] ?? model.exposure_change.direction
}

function flipPriceReason(model: SystematicRiskModel): string {
  const reason = model.flip_price_reason_code
  return reason ? flipPriceReasons[reason] ?? '没有可验证的正翻转价' : ''
}

function modelsFor(item: SystematicRiskSymbol | undefined): SystematicRiskModel[] {
  return item?.models ?? []
}

function baselineIdentity(item: SystematicRiskSymbol | undefined): string {
  const id = item?.baseline.baseline_id
  return id ? `基线 ${id.slice(0, 12)} · ${item?.baseline.last_bar_date ?? '—'} · ${item?.baseline.adjustment ?? '—'}` : ''
}

async function refresh() {
  const currentGeneration = ++generation
  controller?.abort()
  controller = new AbortController()
  loading.value = true
  error.value = ''
  try {
    const result = await getSystematicRiskOverview(controller.signal)
    if (currentGeneration !== generation || controller.signal.aborted) return
    overview.value = result
  } catch (reason) {
    if (currentGeneration === generation && !controller.signal.aborted) {
      error.value = reason instanceof Error ? reason.message : '系统性风险读取失败。'
      overview.value = null
    }
  } finally {
    if (currentGeneration === generation) loading.value = false
  }
}

function itemFor(symbol: string): SystematicRiskSymbol | undefined {
  return overview.value?.symbols[symbol]
}

watch(() => props.refreshToken, () => { void refresh() })

onMounted(() => {
  void refresh()
})

onBeforeUnmount(() => {
  controller?.abort()
})
</script>

<template>
  <section class="systematic-risk-card" :data-status="overview?.status ?? 'loading'" aria-label="CTA 风格系统性风险代理">
    <header class="systematic-risk-heading">
      <div>
        <span class="section-kicker">SYSTEMATIC RISK PROXY</span>
        <h2>CTA 风格趋势风险代理</h2>
      </div>
      <div class="systematic-risk-meta">
        <span>{{ overview ? statusLabel(overview.status) : loading ? '读取中' : '读取失败' }}</span>
        <small>{{ overview?.evaluated_at ?? '—' }}</small>
      </div>
    </header>

    <p class="systematic-risk-disclaimer">
      只展示昨日冻结日线与“假设今日以报价收盘”的情景；不代表真实 CTA 仓位、订单或资金流。
    </p>
    <p v-if="error" class="systematic-risk-error" role="alert">{{ error }}</p>
    <p v-for="reason in overview?.reasons ?? []" :key="reason.code + reason.message" class="systematic-risk-error" role="status">
      {{ reason.message }}
    </p>
    <p v-if="!error && overview?.status === 'disabled'" class="systematic-risk-empty">
      系统性风险代理当前已关闭。
    </p>
    <p v-else-if="!error && !overview && loading" class="systematic-risk-empty">正在读取冻结日线与最新已采集报价…</p>

    <div v-else-if="!error" class="systematic-risk-symbol-grid">
      <article v-for="symbol in symbols" :key="symbol" class="systematic-risk-symbol" :data-status="itemFor(symbol)?.status ?? 'unavailable'">
        <header>
          <strong>{{ symbol }}</strong>
          <span>{{ statusLabel(itemFor(symbol)?.status ?? 'unavailable') }}</span>
        </header>

        <template v-if="itemFor(symbol)?.models.length">
          <div class="systematic-risk-quote">
            <span>假设收盘 <strong>{{ formatPrice(itemFor(symbol)?.assumed_close) }}</strong></span>
            <small>报价 {{ itemFor(symbol)?.quote_as_of ?? '—' }} · 暂行情景</small>
          </div>
          <div class="systematic-risk-model-list">
            <section v-for="model in modelsFor(itemFor(symbol))" :key="model.model_version + model.fast_slow.join('-')" class="systematic-risk-model" :data-available="model.available">
              <header>
                <strong>{{ modelLabel(model) }}</strong>
                <span v-if="model.available">{{ directionLabel(model) }}</span>
                <span v-else>不可用</span>
              </header>
              <small class="systematic-risk-parameters">
                波动率窗口 {{ model.parameters.volatility_window }} 日 · 目标 {{ formatPercent(model.parameters.target_vol, 0) }} · 敞口上限 {{ formatPercent(model.parameters.cap, 0) }}
              </small>
              <div v-if="model.available" class="systematic-risk-metrics">
                <span>基线 {{ trendLabel(model.baseline.trend_signal) }} · 敞口 {{ formatPercent(model.baseline.target_exposure, 0) }}</span>
                <span>情景 {{ trendLabel(model.scenario.trend_signal) }} · 敞口 {{ formatPercent(model.scenario.target_exposure, 0) }}</span>
                <span>年化波动 {{ formatPercent(model.scenario.annualized_volatility) }}</span>
                <span>敞口变化分解（pp）：趋势贡献 {{ signedPercentagePoints(model.exposure_change.trend_only) }} · 波动贡献 {{ signedPercentagePoints(model.exposure_change.volatility_only) }} · 交互项 {{ signedPercentagePoints(model.exposure_change.interaction) }} · 联合变化 {{ signedPercentagePoints(model.exposure_change.joint) }}</span>
                <span>条件翻转价 {{ formatPrice(model.flip_price) }}<template v-if="flipPriceReason(model)"> · {{ flipPriceReason(model) }}</template></span>
              </div>
              <p v-else class="systematic-risk-model-reason">{{ model.message }}</p>
            </section>
          </div>
          <details class="systematic-risk-identity">
            <summary>冻结基线与来源</summary>
            <small>{{ baselineIdentity(itemFor(symbol)) }}</small>
            <small>Dataset {{ itemFor(symbol)?.baseline.dataset_id ?? '—' }}</small>
            <small>Dataset hash {{ itemFor(symbol)?.baseline.dataset_hash ?? '—' }}</small>
            <small>Chart hash {{ itemFor(symbol)?.baseline.chart_hash ?? '—' }}</small>
            <small>输入 hash {{ itemFor(symbol)?.baseline.input_hash ?? '—' }}</small>
          </details>
          <ul v-if="itemFor(symbol)?.reasons.length" class="systematic-risk-reasons">
            <li v-for="reason in itemFor(symbol)?.reasons" :key="reason.code + reason.message">{{ reason.message }}</li>
          </ul>
        </template>
        <ul v-else class="systematic-risk-reasons">
          <li v-for="reason in itemFor(symbol)?.reasons ?? []" :key="reason.code + reason.message">{{ reason.message }}</li>
          <li v-if="!itemFor(symbol)?.reasons.length">当前没有可用的冻结日线与报价组合。</li>
        </ul>
      </article>
    </div>
  </section>
</template>
