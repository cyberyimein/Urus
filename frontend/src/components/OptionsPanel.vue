<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

import MockBadge from '@/components/MockBadge.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import type {
  ExposureStrikeRow,
  ExposureWall,
  OptionExpirationAnalysis,
  OptionSymbolAnalysis,
  OptionsData,
} from '@/types/api'
import { formatDate, formatNumber } from '@/utils/format'

const props = defineProps<{ options: OptionsData }>()

const selectedSymbol = ref('')
const selectedExpiration = ref('')
const exposureScroller = ref<HTMLElement | null>(null)
const liveOptions = computed(() => (props.options.is_mock ? null : props.options))
const symbols = computed(() => liveOptions.value?.symbols ?? [])

watch(
  symbols,
  (items) => {
    if (!items.some((item) => item.symbol === selectedSymbol.value)) {
      selectedSymbol.value = items[0]?.symbol ?? ''
    }
  },
  { immediate: true },
)

const currentSymbol = computed<OptionSymbolAnalysis | null>(
  () => symbols.value.find((item) => item.symbol === selectedSymbol.value) ?? symbols.value[0] ?? null,
)

watch(
  currentSymbol,
  (item) => {
    if (!item?.expirations.some((expiry) => expiry.expiration === selectedExpiration.value)) {
      selectedExpiration.value = item?.expirations[0]?.expiration ?? ''
    }
  },
  { immediate: true },
)

const currentExpiry = computed<OptionExpirationAnalysis | null>(
  () =>
    currentSymbol.value?.expirations.find(
      (item) => item.expiration === selectedExpiration.value,
    ) ??
    currentSymbol.value?.expirations[0] ??
    null,
)

const strikeRows = computed<ExposureStrikeRow[]>(() => currentExpiry.value?.exposure.by_strike ?? [])
const maxDex = computed(() => Math.max(1, ...strikeRows.value.map((item) => Math.abs(item.net_dex))))
const maxGex = computed(() =>
  Math.max(1, ...strikeRows.value.map((item) => Math.abs(item.modeled_net_gex))),
)
const nearestSpotStrike = computed<number | null>(() => {
  if (!currentSymbol.value || !strikeRows.value.length) return null
  return strikeRows.value.reduce((nearest, row) =>
    Math.abs(row.strike - currentSymbol.value!.spot) <
    Math.abs(nearest - currentSymbol.value!.spot)
      ? row.strike
      : nearest,
  strikeRows.value[0].strike)
})

async function centerExposureChart(): Promise<void> {
  await nextTick()
  window.requestAnimationFrame(() => {
    window.requestAnimationFrame(() => {
      const scroller = exposureScroller.value
      const spotColumn = scroller?.querySelector<HTMLElement>('[data-spot="true"]')
      if (!scroller || !spotColumn) return
      scroller.scrollLeft = Math.max(
        0,
        spotColumn.offsetLeft - scroller.clientWidth / 2 + spotColumn.offsetWidth / 2,
      )
    })
  })
}

watch(
  () => [selectedSymbol.value, selectedExpiration.value, strikeRows.value.length],
  () => void centerExposureChart(),
  { flush: 'post', immediate: true },
)

const focusWallLabels: Record<string, string> = {
  call_dex: 'Call DEX Wall',
  put_dex: 'Put DEX Wall',
  net_dex: 'Net DEX Wall',
  call_gamma: 'Call Gamma Wall',
  put_gamma: 'Put Gamma Wall',
  absolute_gamma: 'Gamma Wall',
}

function overview(key: string): number | null {
  return currentSymbol.value?.overview[key] ?? null
}

function ratio(numerator: string, denominator: string): number | null {
  const top = overview(numerator)
  const bottom = overview(denominator)
  return top === null || bottom === null || bottom === 0 ? null : top / bottom
}

function compactNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '不可用'
  return new Intl.NumberFormat('zh-CN', { notation: 'compact', maximumFractionDigits: 2 }).format(value)
}

function signedCompact(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '不可用'
  return `${value >= 0 ? '+' : ''}${compactNumber(value)}`
}

function wall(key: string): ExposureWall | null {
  return currentExpiry.value?.exposure.walls[key] ?? null
}

function wallLabel(key: string): string {
  const item = wall(key)
  return item ? `${formatNumber(item.strike)} · ${signedCompact(item.exposure)}` : '不可用'
}

function barHeight(value: number, max: number): string {
  return `${Math.max(1, Math.abs(value) / max * 82)}px`
}

function sameStrike(left: number | null | undefined, right: number): boolean {
  return left !== null && left !== undefined && Math.abs(left - right) < 0.0001
}

function focusReasons(row: ExposureStrikeRow): string[] {
  const reasons: string[] = []
  if (sameStrike(nearestSpotStrike.value, row.strike)) reasons.push('现价附近')
  if (sameStrike(currentExpiry.value?.max_pain, row.strike)) reasons.push('Max Pain')
  for (const [key, label] of Object.entries(focusWallLabels)) {
    if (sameStrike(wall(key)?.strike, row.strike)) reasons.push(label)
  }
  return [...new Set(reasons)]
}

function focusRowClass(row: ExposureStrikeRow): Record<string, boolean> {
  const reasons = focusReasons(row)
  return {
    'focus-row': reasons.length > 0,
    'focus-max-pain': reasons.includes('Max Pain'),
    'focus-wall': reasons.some((reason) => reason.includes('Wall')),
    'focus-spot': reasons.includes('现价附近'),
  }
}

function focusShortLabels(row: ExposureStrikeRow): string[] {
  const labels = focusReasons(row).map((reason) => {
    if (reason === '现价附近') return 'ATM'
    if (reason === 'Max Pain') return 'MP'
    if (reason.includes('DEX')) return 'DEX'
    if (reason.includes('Gamma')) return 'Γ'
    return reason
  })
  return [...new Set(labels)]
}

function showStrikeLabel(index: number, row: ExposureStrikeRow): boolean {
  const interval = Math.max(1, Math.ceil(strikeRows.value.length / 22))
  return focusReasons(row).length > 0 || index % interval === 0
}

function gammaRegimeLabel(regime: ExposureStrikeRow['gamma_regime']): string {
  if (regime === 'positive') return '正 Gamma'
  if (regime === 'negative') return '负 Gamma'
  return '中性'
}

function gammaFlipAtRow(index: number): number | null {
  if (index <= 0 || !currentExpiry.value) return null
  const previousStrike = strikeRows.value[index - 1].strike
  const strike = strikeRows.value[index].strike
  return currentExpiry.value.exposure.gamma_flip_levels.find(
    (item) => item.level > previousStrike && item.level <= strike,
  )?.level ?? null
}

function gammaZoneSummary(sign: 'positive' | 'negative'): string {
  const zones = currentExpiry.value?.exposure.gamma_zones.filter((item) => item.sign === sign) ?? []
  if (!zones.length) return '无显著区间'
  return zones
    .map((zone) => zone.start_strike === zone.end_strike
      ? formatNumber(zone.start_strike)
      : `${formatNumber(zone.start_strike)}–${formatNumber(zone.end_strike)}`)
    .join('、')
}
</script>

<template>
  <section class="tab-panel options-panel" role="tabpanel">
    <div class="tab-titlebar">
      <div><p class="eyebrow">COLLECTED / STAGE 2</p><h2>期权结构数据</h2></div>
      <div class="heading-meta">
        <span v-if="liveOptions" class="live-badge">Moomoo LV1 Snapshot</span>
        <MockBadge v-else />
        <StatusBadge :status="options.status" />
      </div>
    </div>

    <template v-if="liveOptions && currentSymbol">
      <section class="data-section">
        <div class="section-label-row">
          <div><span class="section-kicker">UNDERLYING</span><h3>采集标的</h3></div>
          <span class="source-label">{{ liveOptions.provider }} · {{ symbols.length }}/{{ liveOptions.requested_symbols.length }} 返回 · {{ formatDate(liveOptions.captured_at) }}</span>
        </div>
        <div class="option-selector" role="tablist" aria-label="期权标的">
          <button
            v-for="item in symbols"
            :key="item.symbol"
            type="button"
            :class="{ active: selectedSymbol === item.symbol }"
            @click="selectedSymbol = item.symbol"
          >
            <strong>{{ item.symbol }}</strong><small>{{ formatNumber(item.spot) }}</small>
          </button>
        </div>
        <div v-if="liveOptions.unavailable_symbols.length" class="notice-box warning-box"><strong>未返回期权链</strong><span>{{ liveOptions.unavailable_symbols.join('、') }}</span></div>
      </section>

      <section class="data-section">
        <div class="section-label-row"><div><span class="section-kicker">OVERVIEW</span><h3>{{ currentSymbol.symbol }} 期权总览</h3></div><span class="source-label">标的报价：{{ currentSymbol.spot_time || '不可用' }}</span></div>
        <div class="metric-grid options-overview-grid">
          <div class="metric-cell metric-cell-major"><span>标的现价</span><strong>{{ formatNumber(currentSymbol.spot) }}</strong><small>{{ currentSymbol.symbol }}</small></div>
          <div class="metric-cell"><span>综合 IV</span><strong>{{ formatNumber(overview('iv')) }}%</strong><small>最新快照</small></div>
          <div class="metric-cell"><span>IV Rank</span><strong>{{ formatNumber(overview('iv_rank')) }}%</strong><small>历史区间位置</small></div>
          <div class="metric-cell"><span>IV Percentile</span><strong>{{ formatNumber(overview('iv_percentile')) }}%</strong><small>历史百分位</small></div>
          <div class="metric-cell"><span>Volume P/C</span><strong>{{ formatNumber(ratio('put_volume', 'call_volume')) }}</strong><small>{{ compactNumber(overview('put_volume')) }} / {{ compactNumber(overview('call_volume')) }}</small></div>
          <div class="metric-cell"><span>OI P/C</span><strong>{{ formatNumber(ratio('put_open_interest', 'call_open_interest')) }}</strong><small>{{ compactNumber(overview('put_open_interest')) }} / {{ compactNumber(overview('call_open_interest')) }}</small></div>
        </div>
      </section>

      <section class="data-section">
        <div class="section-label-row"><div><span class="section-kicker">EXPIRATION</span><h3>到期日切片</h3></div><span class="source-label">每个到期日独立计算 Max Pain 与墙位</span></div>
        <div class="expiry-selector">
          <button
            v-for="item in currentSymbol.expirations"
            :key="item.expiration"
            type="button"
            :class="{ active: selectedExpiration === item.expiration }"
            @click="selectedExpiration = item.expiration"
          >
            <strong>{{ item.expiration }}</strong><small>{{ item.days_to_expiry }} DTE · {{ item.contract_count }} 合约</small>
          </button>
        </div>
      </section>

      <template v-if="currentExpiry">
        <section class="data-section">
          <div class="section-label-row"><div><span class="section-kicker">KEY LEVELS</span><h3>关键结构</h3></div><span class="source-label">DEX 使用 Delta 自然符号 · GEX 为模型值</span></div>
          <div class="metric-grid options-level-grid">
            <div class="metric-cell metric-cell-major"><span>Max Pain</span><strong>{{ formatNumber(currentExpiry.max_pain) }}</strong><small>按本到期日 OI 计算</small></div>
            <div class="metric-cell"><span>Expected Move</span><strong>{{ formatNumber(currentExpiry.expected_move.amount) }}</strong><small>{{ formatNumber(currentExpiry.expected_move.percent) }}% · ATM {{ formatNumber(currentExpiry.expected_move.atm_strike) }}</small></div>
            <div class="metric-cell"><span>Net DEX</span><strong :class="currentExpiry.exposure.totals.net_dex >= 0 ? 'positive-text' : 'negative-text'">{{ signedCompact(currentExpiry.exposure.totals.net_dex) }}</strong><small>美元 Delta 等价敞口</small></div>
            <div class="metric-cell"><span>Absolute DEX</span><strong>{{ compactNumber(currentExpiry.exposure.totals.absolute_dex) }}</strong><small>不抵消的 Delta 集中度</small></div>
            <div class="metric-cell"><span>Modeled Net GEX</span><strong :class="currentExpiry.exposure.totals.modeled_net_gex >= 0 ? 'positive-text' : 'negative-text'">{{ signedCompact(currentExpiry.exposure.totals.modeled_net_gex) }}</strong><small>Call + / Put − 假设</small></div>
            <div class="metric-cell"><span>Absolute GEX</span><strong>{{ compactNumber(currentExpiry.exposure.totals.absolute_gex) }}</strong><small>每 1% 现价变化</small></div>
          </div>
        </section>

        <section class="data-section">
          <div class="section-label-row"><div><span class="section-kicker">WALLS</span><h3>DEX 与 Gamma 墙</h3></div><span class="source-label">行权价 · exposure</span></div>
          <div class="wall-grid">
            <div><span>Call DEX Wall</span><strong>{{ wallLabel('call_dex') }}</strong></div>
            <div><span>Put DEX Wall</span><strong>{{ wallLabel('put_dex') }}</strong></div>
            <div><span>Net DEX Wall</span><strong>{{ wallLabel('net_dex') }}</strong></div>
            <div><span>Call Gamma Wall</span><strong>{{ wallLabel('call_gamma') }}</strong></div>
            <div><span>Put Gamma Wall</span><strong>{{ wallLabel('put_gamma') }}</strong></div>
            <div><span>Absolute Gamma Wall</span><strong>{{ wallLabel('absolute_gamma') }}</strong></div>
          </div>
        </section>

        <section class="data-section">
          <div class="section-label-row"><div><span class="section-kicker">EXPOSURE MAP</span><h3>行权价结构图</h3></div><span class="source-label">现价居中 · 左低右高 · 零轴上下表示正负</span></div>
          <div class="gamma-zone-summary">
            <div class="positive-zone"><span>正 Gamma 区间</span><strong>{{ gammaZoneSummary('positive') }}</strong></div>
            <div class="negative-zone"><span>负 Gamma 区间</span><strong>{{ gammaZoneSummary('negative') }}</strong></div>
            <div><span>建模 Gamma Flip</span><strong>{{ currentExpiry.exposure.gamma_flip_levels.length ? currentExpiry.exposure.gamma_flip_levels.map((item) => formatNumber(item.level)).join('、') : '未出现' }}</strong></div>
          </div>
          <div class="horizontal-chart-key"><span><i class="dex-key"></i>Net DEX</span><span><i class="gex-key"></i>Modeled Net GEX</span><span><i class="positive-zone-key"></i>正 Gamma 区间</span><span><i class="negative-zone-key"></i>负 Gamma 区间</span><span><i class="flip-key"></i>建模 Flip</span></div>
          <div ref="exposureScroller" class="horizontal-exposure-scroll">
            <div class="horizontal-exposure-chart">
              <span class="horizontal-zero-axis"></span>
              <div
                v-for="(row, index) in strikeRows"
                :key="row.strike"
                class="strike-column"
                :class="[focusRowClass(row), `gamma-${row.gamma_regime}`]"
                :data-spot="sameStrike(nearestSpotStrike, row.strike)"
                :title="`${formatNumber(row.strike)} · DEX ${signedCompact(row.net_dex)} · GEX ${signedCompact(row.modeled_net_gex)}${focusReasons(row).length ? ` · ${focusReasons(row).join(' / ')}` : ''}`"
              >
                <span v-if="gammaFlipAtRow(index) !== null" class="gamma-flip-marker"><small>Γ FLIP</small></span>
                <div class="vertical-exposure-bars">
                  <span class="vertical-bar dex-vertical" :class="row.net_dex >= 0 ? 'positive-bar' : 'negative-bar'" :style="{ height: barHeight(row.net_dex, maxDex), bottom: row.net_dex >= 0 ? '50%' : 'auto', top: row.net_dex < 0 ? '50%' : 'auto' }"></span>
                  <span class="vertical-bar gex-vertical" :class="row.modeled_net_gex >= 0 ? 'positive-bar' : 'negative-bar'" :style="{ height: barHeight(row.modeled_net_gex, maxGex), bottom: row.modeled_net_gex >= 0 ? '50%' : 'auto', top: row.modeled_net_gex < 0 ? '50%' : 'auto' }"></span>
                </div>
                <span class="horizontal-strike-label" :class="{ 'spot-nearby': sameStrike(nearestSpotStrike, row.strike) }">{{ showStrikeLabel(index, row) ? formatNumber(row.strike, 0) : '' }}</span>
                <span class="horizontal-focus-badges"><small v-for="label in focusShortLabels(row)" :key="label">{{ label }}</small></span>
              </div>
            </div>
          </div>
        </section>

        <section class="data-section">
          <div class="section-label-row"><div><span class="section-kicker">RAW AGGREGATION</span><h3>按行权价聚合</h3></div><span class="source-label">{{ currentExpiry.exposure.usable_delta_contracts }} Delta · {{ currentExpiry.exposure.usable_gamma_contracts }} Gamma</span></div>
          <div class="table-wrap">
            <table class="data-table options-table">
              <thead><tr><th>Strike</th><th>关注</th><th>Gamma 区间</th><th>Call DEX</th><th>Put DEX</th><th>Net DEX</th><th>Abs DEX</th><th>Call GEX</th><th>Put GEX</th><th>Modeled Net GEX</th><th>Abs GEX</th></tr></thead>
              <tbody><tr v-for="row in strikeRows" :key="row.strike" :class="[focusRowClass(row), `gamma-${row.gamma_regime}`]"><td><strong>{{ formatNumber(row.strike) }}</strong></td><td><span class="focus-badges"><small v-for="reason in focusReasons(row)" :key="reason" class="focus-tag">{{ reason }}</small><small v-if="!focusReasons(row).length" class="focus-empty">—</small></span></td><td><small class="gamma-regime-tag" :class="`gamma-${row.gamma_regime}`">{{ gammaRegimeLabel(row.gamma_regime) }}</small></td><td>{{ signedCompact(row.call_dex) }}</td><td>{{ signedCompact(row.put_dex) }}</td><td>{{ signedCompact(row.net_dex) }}</td><td>{{ compactNumber(row.absolute_dex) }}</td><td>{{ compactNumber(row.call_gex) }}</td><td>{{ compactNumber(row.put_gex) }}</td><td>{{ signedCompact(row.modeled_net_gex) }}</td><td>{{ compactNumber(row.absolute_gex) }}</td></tr></tbody>
            </table>
          </div>
        </section>
      </template>

      <section class="data-section">
        <div class="section-label-row"><div><span class="section-kicker">QUALITY & ASSUMPTIONS</span><h3>数据边界</h3></div><span class="source-label">订阅占用 {{ liveOptions.subscription_quota.option_used_quota ?? '不可用' }} / 剩余 {{ liveOptions.subscription_quota.option_remain_quota ?? '不可用' }}</span></div>
        <div class="notice-list"><div v-for="warning in liveOptions.warnings" :key="warning" class="notice-box warning-box"><strong>提示</strong><span>{{ warning }}</span></div></div>
        <div class="notice-list assumptions-list"><div v-for="assumption in liveOptions.model_assumptions" :key="assumption" class="notice-box"><strong>模型</strong><span>{{ assumption }}</span></div></div>
      </section>

      <section class="data-section unfinished-section">
        <div class="section-label-row"><div><span class="section-kicker">NOT COLLECTED</span><h3>明确不在本模块</h3></div></div>
        <div class="unfinished-list"><span>VEX / Vanna</span><span>做市商真实净仓位</span><span>开仓 / 平仓方向</span><span>多腿成交识别</span><span>逐笔期权历史</span><span>OptionCharts 依赖</span></div>
      </section>
    </template>

    <div v-else class="empty-panel options-empty"><h3>期权数据尚未采集</h3><p>{{ options.note }}</p></div>
  </section>
</template>
