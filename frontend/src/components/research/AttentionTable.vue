<script setup lang="ts">
import { computed, ref } from 'vue'

import { optionFor, record, text } from './reportHelpers'

type Ranking = Record<string, unknown>

const props = defineProps<{
  rankings: Ranking[]
  optionContext: Ranking[]
  manual?: boolean
  selectedSymbol?: string
}>()

const emit = defineEmits<{
  (event: 'select-symbol', symbol: string): void
}>()

const filter = ref<'all' | 'watch' | 'observe' | 'avoid'>('all')
const sortBy = ref<'rank' | 'score' | 'risk'>('rank')

const rows = computed(() => props.rankings.map((item, index) => {
  const option = optionFor(props.optionContext, item.symbol)
  const pricing = record(option.volatility_pricing)
  const risks = Array.isArray(item.risks) ? item.risks.length : 0
  const missing = Array.isArray(item.missing_fields) ? item.missing_fields.length : 0
  const rawAction = String(item.action ?? item.status ?? 'observe').toLowerCase()
  const status = props.manual
    ? rawAction === 'avoid' ? 'avoid' : rawAction === 'watch' ? 'watch' : 'observe'
    : ['buy', 'add', 'hold', 'watch', 'setup_ready'].includes(rawAction) ? 'watch' : ['avoid', 'reduce', 'exit', 'stop_loss'].includes(rawAction) ? 'avoid' : 'observe'
  return {
    item,
    symbol: text(item.symbol),
    rank: Number.isFinite(Number(item.rank)) ? Number(item.rank) : index + 1,
    score: Number.isFinite(Number(item.score)) ? Number(item.score) : null,
    status,
    statusLabel: status === 'avoid' ? '回避' : status === 'watch' ? '关注' : '观察',
    reason: text(item.thesis ?? item.reason ?? item.rationale, '未提供理由'),
    ivHv: text(pricing.iv_hv_regime ?? pricing.iv_hv_spread, '—'),
    gamma: text(option.gamma_regime, '—'),
    riskCount: risks + missing,
  }
}))

const filteredRows = computed(() => rows.value
  .filter((row) => filter.value === 'all' || row.status === filter.value)
  .sort((left, right) => {
    if (sortBy.value === 'score') return (right.score ?? -Infinity) - (left.score ?? -Infinity)
    if (sortBy.value === 'risk') return right.riskCount - left.riskCount
    return left.rank - right.rank
  }))

function select(row: { symbol: string }) {
  if (row.symbol !== '—') emit('select-symbol', row.symbol)
}
</script>

<template>
  <section class="attention-section">
    <div class="decision-section-heading attention-heading">
      <div>
        <p class="eyebrow">{{ manual ? 'CURRENT WATCHLIST' : 'EQUITY RANKING' }}</p>
        <h2>{{ manual ? '关注标的' : '大盘与个股排序' }}</h2>
      </div>
      <div class="attention-controls">
        <label class="compact-select"><span>筛选</span><select v-model="filter"><option value="all">全部</option><option value="watch">关注</option><option value="observe">观察</option><option value="avoid">回避</option></select></label>
        <label class="compact-select"><span>排序</span><select v-model="sortBy"><option value="rank">Rank</option><option value="score">Score</option><option value="risk">风险</option></select></label>
      </div>
    </div>

    <div v-if="filteredRows.length" class="attention-table-wrap">
      <table class="attention-table">
        <thead><tr><th>Rank</th><th>Symbol</th><th>状态</th><th>Score</th><th>关键理由</th><th>IV/HV</th><th>Gamma</th><th>风险</th></tr></thead>
        <tbody>
          <tr
            v-for="row in filteredRows"
            :key="row.symbol"
            :class="{ 'selected-row': selectedSymbol === row.symbol }"
            tabindex="0"
            @click="select(row)"
            @keydown.enter="select(row)"
          >
            <td class="attention-rank">#{{ row.rank }}</td>
            <td><strong class="mono attention-symbol">{{ row.symbol }}</strong></td>
            <td><span class="attention-status" :data-status="row.status">{{ row.statusLabel }}</span></td>
            <td class="mono">{{ row.score == null ? '—' : row.score }}</td>
            <td class="attention-reason">{{ row.reason }}</td>
            <td class="mono">{{ row.ivHv }}</td>
            <td>{{ row.gamma }}</td>
            <td class="mono">{{ row.riskCount || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-else class="attention-empty">
      <strong>{{ rankings.length ? '当前筛选没有匹配标的' : '本次输出没有关注标的' }}</strong>
      <span>{{ rankings.length ? '调整筛选条件后重试。' : '完整技术证据仍可在技术报告中查看。' }}</span>
    </div>
    <p v-if="filteredRows.length" class="attention-hint">点击行查看判断、期权、风险与证据；完整原始字段不会在列表中展开。</p>
  </section>
</template>
