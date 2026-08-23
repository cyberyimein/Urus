<script setup lang="ts">
import { computed, ref } from 'vue'

import type { ChartSeries, DecisionChartProjection, DailyBar } from '@/types/dailyEvidence'

type RangeKey = '3M' | '6M' | '1Y' | 'ALL'

const props = withDefaults(defineProps<{
  projection: DecisionChartProjection | null
  symbol: string
  range?: RangeKey
  layers?: Record<string, boolean>
  cursorIndex?: number | null
  strategyFilter?: string | null
}>(), {
  range: '6M',
  layers: () => ({
    ma20: true,
    ma50: true,
    ma200: true,
    bollinger: false,
    volume: true,
    rsi: true,
    macd: false,
    relative: true,
  }),
  cursorIndex: undefined,
  strategyFilter: null,
})

const emit = defineEmits<{
  'update:cursorIndex': [value: number | null]
}>()

const localCursorIndex = ref<number | null>(null)
const width = 1000
const height = 642
const left = 54
const right = 18
const plotWidth = width - left - right
const priceTop = 22
const priceHeight = 294
const volumeTop = 342
const volumeHeight = 76
const momentumTop = 454
const momentumHeight = 76
const relativeTop = 552
const relativeHeight = 50
const rangeCounts: Record<RangeKey, number> = { '3M': 63, '6M': 126, '1Y': 252, ALL: Number.MAX_SAFE_INTEGER }

const instrument = computed(() => props.projection?.instruments?.[props.symbol] ?? null)
const bars = computed(() => {
  const source = instrument.value?.price?.bars ?? []
  const count = rangeCounts[props.range]
  return source.slice(-count)
})
const series = computed(() => instrument.value?.series ?? [])
const seriesById = computed(() => {
  const values: Record<string, ChartSeries> = {}
  for (const item of series.value) values[item.series_id] = item
  return values
})
const pointMaps = computed(() => {
  const values: Record<string, Map<string, number | null>> = {}
  for (const item of series.value) {
    values[item.series_id] = new Map(item.points.map((point) => [point.time, point.value]))
  }
  return values
})
const hasData = computed(() => bars.value.length > 0)
const latestBar = computed(() => bars.value[bars.value.length - 1] ?? null)
const activeCursorIndex = computed(() => {
  const requested = props.cursorIndex ?? localCursorIndex.value
  if (requested === null || requested === undefined) return bars.value.length - 1
  return Math.max(0, Math.min(requested, Math.max(0, bars.value.length - 1)))
})
const cursorBar = computed(() => bars.value[activeCursorIndex.value] ?? latestBar.value)
const candleWidth = computed(() => Math.max(1.5, Math.min(8, plotWidth / Math.max(1, bars.value.length) * 0.58)))
const xForIndex = (index: number) => {
  if (bars.value.length <= 1) return left + plotWidth / 2
  return left + (index / (bars.value.length - 1)) * plotWidth
}

function valueFor(seriesId: string, bar: DailyBar | undefined): number | null {
  if (!bar) return null
  const value = pointMaps.value[seriesId]?.get(bar.date)
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

const priceBounds = computed(() => {
  const values: number[] = []
  for (const bar of bars.value) values.push(bar.high, bar.low)
  const priceSeries = ['ma20', 'ma50', 'ma200', 'bollinger_upper_20_2', 'bollinger_lower_20_2']
  for (const id of priceSeries) {
    for (const bar of bars.value) {
      const value = valueFor(id, bar)
      if (value !== null) values.push(value)
    }
  }
  for (const overlay of props.projection?.overlays ?? []) {
    if (overlay.symbol === props.symbol && typeof overlay.price === 'number') values.push(overlay.price)
    if (overlay.symbol === props.symbol && typeof overlay.lower_price === 'number') values.push(overlay.lower_price)
    if (overlay.symbol === props.symbol && typeof overlay.upper_price === 'number') values.push(overlay.upper_price)
  }
  if (!values.length) return { min: 0, max: 1 }
  const min = Math.min(...values)
  const max = Math.max(...values)
  const padding = Math.max((max - min) * 0.08, max * 0.006, 0.1)
  return { min: min - padding, max: max + padding }
})

const volumeMax = computed(() => Math.max(...bars.value.map((bar) => bar.volume), 1))
const macdValues = computed(() => {
  const values: number[] = []
  for (const id of ['macd_dif_12_26', 'macd_dea_9', 'macd_histogram']) {
    for (const bar of bars.value) {
      const value = valueFor(id, bar)
      if (value !== null) values.push(value)
    }
  }
  const max = Math.max(...values.map((value) => Math.abs(value)), 0.01)
  return { min: -max * 1.15, max: max * 1.15 }
})

const priceY = (value: number) => priceTop + (1 - (value - priceBounds.value.min) / (priceBounds.value.max - priceBounds.value.min)) * priceHeight
const volumeY = (value: number) => volumeTop + volumeHeight - (value / volumeMax.value) * volumeHeight
const rsiY = (value: number) => momentumTop + momentumHeight - (value / 100) * momentumHeight
const macdY = (value: number) => momentumTop + momentumHeight - (value - macdValues.value.min) / (macdValues.value.max - macdValues.value.min) * momentumHeight
const relativeY = (value: number) => relativeTop + relativeHeight - (value - 80) / 40 * relativeHeight

function xForTime(value: unknown): number {
  const index = bars.value.findIndex((bar) => bar.date === String(value ?? ''))
  return index >= 0 ? xForIndex(index) : left + plotWidth
}

const priceTicks = computed(() => {
  const result: Array<{ value: number; y: number; label: string }> = []
  for (let index = 0; index < 5; index += 1) {
    const ratio = index / 4
    const value = priceBounds.value.max - ratio * (priceBounds.value.max - priceBounds.value.min)
    result.push({ value, y: priceTop + ratio * priceHeight, label: formatPrice(value) })
  }
  return result
})

const visibleOverlays = computed(() => (props.projection?.overlays ?? []).filter((item) => item.symbol === props.symbol))
const activeStrategySeriesIds = computed(() => {
  const result = new Set<string>()
  if (!props.strategyFilter) return result
  for (const overlay of visibleOverlays.value) {
    if (overlay.strategy_name !== props.strategyFilter || overlay.kind !== 'series_highlight') continue
    if (!Array.isArray(overlay.series_ids)) continue
    for (const seriesId of overlay.series_ids) {
      if (typeof seriesId === 'string') result.add(seriesId)
    }
  }
  return result
})

function hasActiveStrategySeries(prefix: string): boolean {
  return [...activeStrategySeriesIds.value].some((seriesId) => seriesId.startsWith(prefix))
}

const bollingerActive = computed(() => props.layers.bollinger || hasActiveStrategySeries('bollinger_'))
const rsiActive = computed(() => props.layers.rsi || hasActiveStrategySeries('rsi'))
const macdActive = computed(() => props.layers.macd || hasActiveStrategySeries('macd_'))
const relativeActive = computed(() => props.layers.relative || hasActiveStrategySeries('relative_performance'))

const visibleSeries = computed(() => series.value.filter((item) => {
  if (item.series_id === 'ma20') return props.layers.ma20
  if (item.series_id === 'ma50') return props.layers.ma50
  if (item.series_id === 'ma200') return props.layers.ma200
  if (item.series_id.startsWith('bollinger_')) return bollingerActive.value
  if (item.series_id === 'volume' || item.series_id === 'volume_ma20') return false
  if (item.series_id === 'rsi14') return false
  if (item.series_id.startsWith('macd_')) return false
  if (item.series_id.startsWith('relative_performance')) return false
  return false
}))

const rsiSeries = computed(() => {
  if (!rsiActive.value) return undefined
  const selectedRsi = [...activeStrategySeriesIds.value].find((seriesId) => seriesId.startsWith('rsi'))
  return seriesById.value[selectedRsi ?? 'rsi14']
})
const macdSeries = computed(() => macdActive.value ? series.value.filter((item) => item.series_id.startsWith('macd_')) : [])
const relativeSeries = computed(() => relativeActive.value ? series.value.find((item) => item.series_id.startsWith('relative_performance')) : undefined)
const visibleStateSegments = computed(() => (props.projection?.state_segments ?? []).filter((item) => item.symbol === props.symbol))

function linePath(item: ChartSeries | undefined, yScale: (value: number) => number): string {
  if (!item) return ''
  const segments: string[] = []
  let current = ''
  for (let index = 0; index < bars.value.length; index += 1) {
    const value = valueFor(item.series_id, bars.value[index])
    if (value === null) {
      if (current) segments.push(current)
      current = ''
      continue
    }
    current += `${current ? ' L' : 'M'} ${xForIndex(index).toFixed(2)} ${yScale(value).toFixed(2)}`
  }
  if (current) segments.push(current)
  return segments.join(' ')
}

const bollingerAreaPath = computed(() => {
  if (!bollingerActive.value) return ''
  const upper = seriesById.value.bollinger_upper_20_2
  const lower = seriesById.value.bollinger_lower_20_2
  if (!upper || !lower) return ''
  const top: string[] = []
  const bottom: string[] = []
  for (let index = 0; index < bars.value.length; index += 1) {
    const upperValue = valueFor(upper.series_id, bars.value[index])
    const lowerValue = valueFor(lower.series_id, bars.value[index])
    if (upperValue === null || lowerValue === null) continue
    top.push(`${xForIndex(index).toFixed(2)},${priceY(upperValue).toFixed(2)}`)
    bottom.unshift(`${xForIndex(index).toFixed(2)},${priceY(lowerValue).toFixed(2)}`)
  }
  return top.length ? `M ${top.join(' L ')} L ${bottom.join(' L ')} Z` : ''
})

const dateLabels = computed(() => {
  if (!bars.value.length) return []
  const count = Math.min(6, bars.value.length)
  return Array.from({ length: count }, (_, index) => {
    const barIndex = Math.round((index / Math.max(1, count - 1)) * (bars.value.length - 1))
    return { x: xForIndex(barIndex), label: bars.value[barIndex].date.slice(5) }
  })
})

function cursorX() {
  return xForIndex(activeCursorIndex.value)
}

function onPointer(event: MouseEvent) {
  const target = event.currentTarget as SVGElement
  const rect = target.getBoundingClientRect()
  const localX = ((event.clientX - rect.left) / rect.width) * width
  const ratio = Math.max(0, Math.min(1, (localX - left) / plotWidth))
  const index = Math.round(ratio * Math.max(0, bars.value.length - 1))
  localCursorIndex.value = index
  emit('update:cursorIndex', index)
}

function setCursorToLatest() {
  localCursorIndex.value = bars.value.length - 1
  emit('update:cursorIndex', bars.value.length - 1)
}

function formatPrice(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return '—'
  return value >= 1000 ? value.toLocaleString('en-US', { maximumFractionDigits: 0 }) : value.toFixed(2)
}

function formatVolume(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return '—'
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1)}B`
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(0)}K`
  return value.toFixed(0)
}

function seriesColor(seriesId: string): string {
  if (seriesId === 'ma20') return '#f6c344'
  if (seriesId === 'ma50') return '#42a5f5'
  if (seriesId === 'ma200') return '#ab47bc'
  if (seriesId.startsWith('bollinger')) return '#90a4ae'
  if (seriesId.startsWith('relative')) return '#ff8a65'
  return '#c7d1d8'
}

function histogramColor(bar: DailyBar): string {
  const value = valueFor('macd_histogram', bar)
  return value !== null && value >= 0 ? '#26a69a' : '#ef5350'
}
</script>

<template>
  <section class="chart-workspace" aria-label="日 K 图表工作区">
    <div class="chart-workspace-head">
      <div class="chart-legend">
        <span class="legend-candle"><i></i> OHLC</span>
        <span v-if="props.layers.ma20"><i class="legend-line ma20"></i> MA20</span>
        <span v-if="props.layers.ma50"><i class="legend-line ma50"></i> MA50</span>
        <span v-if="props.layers.ma200"><i class="legend-line ma200"></i> MA200</span>
        <span v-if="bollingerActive"><i class="legend-line bollinger"></i> Bollinger</span>
        <span v-if="relativeActive"><i class="legend-line relative"></i> Relative</span>
      </div>
      <button class="chart-latest-button" type="button" @click="setCursorToLatest">回到最新</button>
    </div>

    <div v-if="!hasData" class="chart-empty">
      <span class="chart-empty-icon">∿</span>
      <strong>还没有可绘制的完整日 K</strong>
      <small>先冻结 Daily Decision Dataset，图表会从同一份证据投影生成。</small>
    </div>

    <svg v-else class="decision-chart" :viewBox="`0 0 ${width} ${height}`" role="img" :aria-label="`${props.symbol} 日 K 图表`" @mousemove="onPointer" @mouseleave="setCursorToLatest">
      <defs>
        <linearGradient id="priceGlow" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stop-color="#2a3640" stop-opacity=".34" />
          <stop offset="1" stop-color="#11161b" stop-opacity="0" />
        </linearGradient>
      </defs>
      <rect x="0" y="0" :width="width" :height="height" fill="#11161b" />
      <rect :x="left" :y="priceTop" :width="plotWidth" :height="priceHeight" fill="url(#priceGlow)" />

      <g class="chart-grid">
        <line v-for="tick in priceTicks" :key="`price-${tick.value}`" :x1="left" :x2="left + plotWidth" :y1="tick.y" :y2="tick.y" />
        <line :x1="left" :x2="left + plotWidth" :y1="volumeTop + volumeHeight" :y2="volumeTop + volumeHeight" />
        <line :x1="left" :x2="left + plotWidth" :y1="momentumTop + momentumHeight" :y2="momentumTop + momentumHeight" />
        <line v-for="label in dateLabels" :key="label.label" :x1="label.x" :x2="label.x" :y1="priceTop" :y2="relativeTop + relativeHeight" class="chart-grid-vertical" />
      </g>

      <path v-if="bollingerAreaPath" :d="bollingerAreaPath" class="bollinger-area" />
      <g class="candle-layer">
        <g v-for="(bar, index) in bars" :key="bar.date">
          <line :x1="xForIndex(index)" :x2="xForIndex(index)" :y1="priceY(bar.high)" :y2="priceY(bar.low)" :class="bar.close >= bar.open ? 'candle-up' : 'candle-down'" />
          <rect :x="xForIndex(index) - candleWidth / 2" :y="Math.min(priceY(bar.open), priceY(bar.close))" :width="candleWidth" :height="Math.max(1.2, Math.abs(priceY(bar.close) - priceY(bar.open)))" :class="bar.close >= bar.open ? 'candle-body-up' : 'candle-body-down'" />
        </g>
      </g>
      <path v-for="item in visibleSeries" :key="item.series_id" :d="linePath(item, priceY)" class="chart-line" :class="[`series-${item.series_id}`, { 'series-strategy-highlight': activeStrategySeriesIds.has(item.series_id) }]" :stroke="seriesColor(item.series_id)" />
      <g v-if="visibleOverlays.length" class="strategy-overlay-layer">
        <g v-for="overlay in visibleOverlays" :key="String(overlay.overlay_id)" :class="{ 'strategy-overlay-dim': Boolean(props.strategyFilter && overlay.strategy_name !== props.strategyFilter) }">
          <title>{{ overlay.strategy_name }} v{{ overlay.strategy_version }} · {{ overlay.stance }} · {{ overlay.action }} · {{ overlay.label }}{{ overlay.reason ? ` · ${overlay.reason}` : '' }}</title>
          <rect v-if="overlay.kind === 'price_zone' && typeof overlay.lower_price === 'number' && typeof overlay.upper_price === 'number'" :x="xForTime(overlay.start_time)" :y="priceY(Number(overlay.upper_price))" :width="Math.max(1, xForTime(overlay.end_time) - xForTime(overlay.start_time))" :height="Math.max(1, priceY(Number(overlay.lower_price)) - priceY(Number(overlay.upper_price)))" :class="[`strategy-overlay-zone`, `strategy-overlay-${String(overlay.tone ?? 'neutral')}`]" />
          <line v-if="typeof overlay.price === 'number' && !['trigger_marker', 'evidence_marker'].includes(String(overlay.kind))" :x1="xForTime(overlay.start_time)" :x2="xForTime(overlay.end_time)" :y1="priceY(Number(overlay.price))" :y2="priceY(Number(overlay.price))" :class="[`strategy-overlay-${String(overlay.tone ?? 'neutral')}`, `strategy-overlay-kind-${String(overlay.kind ?? 'marker')}`]" />
          <circle v-if="typeof overlay.price === 'number' && ['trigger_marker', 'evidence_marker'].includes(String(overlay.kind))" :cx="xForTime(overlay.start_time)" :cy="priceY(Number(overlay.price))" :r="String(overlay.kind) === 'trigger_marker' ? 4.5 : 3" :class="`strategy-overlay-${String(overlay.tone ?? 'neutral')}`" />
          <text v-if="typeof overlay.price === 'number'" :x="xForTime(overlay.start_time) + 7" :y="priceY(Number(overlay.price)) - 4">{{ overlay.label }}</text>
          <text v-else-if="overlay.kind === 'price_zone' && typeof overlay.upper_price === 'number'" :x="xForTime(overlay.start_time) + 7" :y="priceY(Number(overlay.upper_price)) - 4">{{ overlay.label }}</text>
        </g>
      </g>

      <g v-if="props.layers.volume" class="volume-layer">
        <rect v-for="(bar, index) in bars" :key="`volume-${bar.date}`" :x="xForIndex(index) - candleWidth / 2" :y="volumeY(bar.volume)" :width="candleWidth" :height="Math.max(1, volumeTop + volumeHeight - volumeY(bar.volume))" :class="bar.close >= bar.open ? 'volume-up' : 'volume-down'" />
      </g>
      <path v-if="props.layers.volume" :d="linePath(seriesById.volume_ma20, volumeY)" class="volume-average" />

      <g v-if="rsiSeries" class="rsi-layer">
        <line v-for="level in [30, 50, 70]" :key="`rsi-${level}`" :x1="left" :x2="left + plotWidth" :y1="rsiY(level)" :y2="rsiY(level)" class="rsi-reference" />
        <path :d="linePath(rsiSeries, rsiY)" class="rsi-line" :class="{ 'series-strategy-highlight': activeStrategySeriesIds.has(rsiSeries.series_id) }" />
      </g>
      <g v-if="macdSeries.length" class="macd-layer">
        <line :x1="left" :x2="left + plotWidth" :y1="macdY(0)" :y2="macdY(0)" class="macd-zero" />
        <rect v-for="(bar, index) in bars" :key="`macd-${bar.date}`" :x="xForIndex(index) - candleWidth / 2" :y="Math.min(macdY(0), macdY(valueFor('macd_histogram', bar) ?? 0))" :width="candleWidth" :height="Math.max(1, Math.abs(macdY(valueFor('macd_histogram', bar) ?? 0) - macdY(0)))" :fill="histogramColor(bar)" class="macd-histogram" />
        <path :d="linePath(seriesById.macd_dif_12_26, macdY)" class="macd-dif" :class="{ 'series-strategy-highlight': activeStrategySeriesIds.has('macd_dif_12_26') }" />
        <path :d="linePath(seriesById.macd_dea_9, macdY)" class="macd-dea" :class="{ 'series-strategy-highlight': activeStrategySeriesIds.has('macd_dea_9') }" />
      </g>
      <g v-if="relativeSeries" class="relative-layer">
        <line :x1="left" :x2="left + plotWidth" :y1="relativeY(100)" :y2="relativeY(100)" class="relative-reference" />
        <path :d="linePath(relativeSeries, relativeY)" class="relative-line" :class="{ 'series-strategy-highlight': activeStrategySeriesIds.has(relativeSeries.series_id) }" />
      </g>

      <g class="chart-axis-labels">
        <text v-for="tick in priceTicks" :key="`price-label-${tick.value}`" :x="width - 4" :y="tick.y + 3" text-anchor="end">{{ tick.label }}</text>
        <text :x="left" :y="volumeTop - 10">VOLUME</text>
        <text :x="left" :y="momentumTop - 10">{{ macdActive ? 'MACD' : rsiSeries ? rsiSeries.series_id.toUpperCase() : relativeActive ? 'RELATIVE' : 'MOMENTUM' }}</text>
        <text v-if="relativeSeries" :x="left" :y="relativeTop - 8">RELATIVE STRENGTH · QQQ</text>
        <text v-for="label in dateLabels" :key="`date-label-${label.label}`" :x="label.x" :y="height - 12" text-anchor="middle">{{ label.label }}</text>
      </g>

      <g v-if="activeCursorIndex >= 0 && cursorBar" class="chart-crosshair">
        <line :x1="cursorX()" :x2="cursorX()" :y1="priceTop" :y2="height - 24" />
        <circle :cx="cursorX()" :cy="priceY(cursorBar.close)" r="3.5" />
        <g :transform="`translate(${Math.min(cursorX() + 12, width - 190)}, ${priceTop + 12})`">
          <rect width="178" height="74" rx="5" />
          <text x="10" y="17" class="tooltip-date">{{ cursorBar.date }}</text>
          <text x="10" y="35">O {{ formatPrice(cursorBar.open) }}　H {{ formatPrice(cursorBar.high) }}</text>
          <text x="10" y="51">L {{ formatPrice(cursorBar.low) }}　C {{ formatPrice(cursorBar.close) }}</text>
          <text x="10" y="67">VOL {{ formatVolume(cursorBar.volume) }}</text>
        </g>
      </g>
    </svg>

    <div class="chart-status-line">
      <span><i class="status-dot"></i> 已完成日 K · {{ bars.length }} bars</span>
      <span v-if="latestBar">As of {{ latestBar.date }} · {{ formatPrice(latestBar.close) }}</span>
      <span v-if="cursorBar && activeCursorIndex !== bars.length - 1">光标 {{ cursorBar.date }}</span>
    </div>
    <div v-if="visibleStateSegments.length" class="chart-state-strip" aria-label="策略状态时间带">
      <span class="chart-state-label">STRATEGY STATE</span>
      <span v-for="segment in visibleStateSegments" :key="String(segment.segment_id)" class="chart-state-segment" :class="{ 'chart-state-segment-dim': Boolean(props.strategyFilter && segment.strategy_name !== props.strategyFilter) }" :data-state="String(segment.state)">{{ segment.label }} · {{ segment.start_time }}</span>
    </div>
  </section>
</template>
