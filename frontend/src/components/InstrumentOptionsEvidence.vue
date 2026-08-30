<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type {
  ExposureWall,
  OptionExpirationAnalysis,
  OptionSymbolAnalysis,
  OptionsAnalysis,
  OptionsData,
} from '@/types/api'
import type { PostCloseOptionAlignment as PostCloseOptionAlignmentData } from '@/types/research'
import { formatDate, formatNumber, runTypeLabel } from '@/utils/format'

const props = defineProps<{
  options: OptionsData | null
  alignment?: PostCloseOptionAlignmentData | null
  symbol: string
  loading?: boolean
  error?: string
  sourceRunType?: string | null
  sourceCutoff?: string | null
}>()

type MarkerTone = 'max-pain' | 'call-dex' | 'put-dex' | 'net-dex' | 'close' | 'spot'

interface LevelMarker {
  key: string
  label: string
  value: number
  tone: MarkerTone
  near: boolean
  distancePercent: number | null
  lane: number
}

const selectedExpiration = ref('')

const liveOptions = computed<OptionsAnalysis | null>(() => {
  const value = props.options
  return value && !value.is_mock ? value : null
})

const currentSymbol = computed<OptionSymbolAnalysis | null>(() => {
  const target = props.symbol.trim().toUpperCase()
  return liveOptions.value?.symbols.find((item) => item.symbol.toUpperCase() === target) ?? null
})

watch(
  currentSymbol,
  (item) => {
    if (!item?.expirations.some((expiry) => expiry.expiration === selectedExpiration.value)) {
      selectedExpiration.value = item?.expirations[0]?.expiration ?? ''
    }
  },
  { immediate: true },
)

const currentExpiry = computed<OptionExpirationAnalysis | null>(() =>
  currentSymbol.value?.expirations.find((item) => item.expiration === selectedExpiration.value)
  ?? currentSymbol.value?.expirations[0]
  ?? null,
)

const currentAlignmentSymbol = computed(() => {
  const target = props.symbol.trim().toUpperCase()
  return props.alignment?.symbols.find((item) => item.symbol.toUpperCase() === target) ?? null
})

const currentAlignmentExpiration = computed(() =>
  currentAlignmentSymbol.value?.expirations.find((item) => item.expiration === currentExpiry.value?.expiration) ?? null,
)

const regularClose = computed<number | null>(() => {
  const value = currentAlignmentSymbol.value?.close_price
  return typeof value === 'number' && Number.isFinite(value) ? value : null
})

const comparisonPrice = computed<number | null>(() => regularClose.value ?? currentSymbol.value?.spot ?? null)
const usesRegularClose = computed(() => regularClose.value !== null)
const comparisonPriceLabel = computed(() => usesRegularClose.value ? 'REGULAR CLOSE' : 'OPTION SPOT')
const comparisonPriceKind = computed(() => usesRegularClose.value
  ? '官方 regular close · 收盘后对照'
  : '期权快照参考价 · 官方收盘价缺失')
const comparisonDistanceLabel = computed(() => usesRegularClose.value ? '收盘价距离' : 'OPTION SPOT 距离')
const mapComparisonLabel = computed(() => usesRegularClose.value
  ? 'regular close vs option levels'
  : 'option spot reference · close unavailable')
const referenceLegendLabel = computed(() => usesRegularClose.value ? '官方收盘价' : 'OPTION SPOT 参考价')
const proximityPercent = computed(() => props.alignment?.proximity_percent ?? 0.6)

const statusTone = computed(() => {
  if (props.loading) return 'loading'
  if (props.error) return 'error'
  if (currentSymbol.value) return 'available'
  if (liveOptions.value) return 'partial'
  return props.options?.is_mock ? 'mock' : 'unavailable'
})

const statusLabel = computed(() => ({
  loading: '读取中',
  error: '读取失败',
  available: '已采集',
  partial: '部分可用',
  mock: '尚未采集',
  unavailable: '不可用',
} as Record<string, string>)[statusTone.value] ?? '不可用')

const alignmentFlags = computed(() => currentAlignmentExpiration.value?.flags ?? [])
const alignmentReady = computed(() => Boolean(
  props.alignment?.available
  && regularClose.value !== null
  && currentAlignmentExpiration.value,
))
const alignmentTone = computed(() => {
  if (!alignmentReady.value) return 'unavailable'
  return alignmentFlags.value.length ? 'flagged' : 'clear'
})

const alignmentLabel = computed(() => {
  if (!props.alignment) return '无回看结果'
  if (!props.alignment.available) return '回看不可用'
  if (regularClose.value === null) return '无法判定'
  if (!currentAlignmentExpiration.value) return '到期日未对齐'
  if (alignmentFlags.value.includes('near_max_pain') && alignmentFlags.value.includes('near_dex_wall')) return '命中两项'
  if (alignmentFlags.value.includes('near_max_pain')) return '接近 Max Pain'
  if (alignmentFlags.value.includes('near_dex_wall')) return 'DEX 影响候选'
  return '未命中'
})

function flagLabel(flag: string): string {
  return ({
    near_max_pain: '收盘价接近 Max Pain',
    near_dex_wall: 'DEX 影响候选',
  } as Record<string, string>)[flag] ?? flag
}

const alignmentDetail = computed(() => {
  if (!props.alignment) return '当前快照没有 post-close 对照结果。'
  if (!props.alignment.available) return 'post-close 回看数据不可用，未执行关键价位邻近判断。'
  if (regularClose.value === null) return '缺少官方 regular close，未把 OPTION SPOT 当作收盘价进行回看。'
  if (!currentAlignmentExpiration.value) return '当前到期日没有可比较的收盘后结构。'
  if (alignmentFlags.value.length) return `${alignmentFlags.value.map(flagLabel).join(' · ')} · 以官方 regular close 判断。`
  return `未接近 Max Pain / DEX Wall · 判定阈值 ±${formatNumber(proximityPercent.value)}%。`
})

function finiteNumber(value: number | null | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function wall(key: string): ExposureWall | null {
  return currentExpiry.value?.exposure.walls[key] ?? null
}

function alignmentWall(kind: string) {
  return currentAlignmentExpiration.value?.dex_walls.find((item) => item.kind === kind) ?? null
}

function distancePercent(value: number, base: number | null): number | null {
  return base === null || base === 0 ? null : Math.abs(value - base) / Math.abs(base) * 100
}

const wallDefinitions: Array<{ key: 'call_dex' | 'put_dex' | 'net_dex'; label: string; tone: MarkerTone }> = [
  { key: 'call_dex', label: 'CALL DEX', tone: 'call-dex' },
  { key: 'put_dex', label: 'PUT DEX', tone: 'put-dex' },
  { key: 'net_dex', label: 'NET DEX', tone: 'net-dex' },
]

const optionLevelMarkers = computed<LevelMarker[]>(() => {
  const expiry = currentExpiry.value
  if (!expiry) return []

  const raw: Array<Omit<LevelMarker, 'lane'>> = []
  const maxPain = finiteNumber(expiry.max_pain)
  if (maxPain !== null) {
    const alignment = currentAlignmentExpiration.value
    raw.push({
      key: 'max-pain',
      label: 'MAX PAIN',
      value: maxPain,
      tone: 'max-pain',
      near: Boolean(alignment?.near_max_pain),
      distancePercent: alignment?.max_pain_distance_percent ?? distancePercent(maxPain, comparisonPrice.value),
    })
  }

  for (const item of wallDefinitions) {
    const value = finiteNumber(wall(item.key)?.strike)
    if (value === null) continue
    const alignment = alignmentWall(item.key)
    raw.push({
      key: item.key,
      label: item.label,
      value,
      tone: item.tone,
      near: Boolean(alignment?.near),
      distancePercent: alignment?.distance_percent ?? distancePercent(value, comparisonPrice.value),
    })
  }

  const grouped: Array<Omit<LevelMarker, 'lane'>> = []
  for (const marker of raw) {
    const existing = grouped.find((item) => Math.abs(item.value - marker.value) < 0.0001)
    if (!existing) {
      grouped.push({ ...marker })
      continue
    }
    if (!existing.label.includes(marker.label)) existing.label = `${existing.label} / ${marker.label}`
    existing.near = existing.near || marker.near
    existing.distancePercent ??= marker.distancePercent
  }

  return grouped
    .sort((left, right) => left.value - right.value)
    .map((marker, index) => ({ ...marker, lane: index % 2 }))
})

const priceMarkers = computed<LevelMarker[]>(() => {
  const markers: LevelMarker[] = []
  const close = finiteNumber(regularClose.value)
  const spot = finiteNumber(currentSymbol.value?.spot)
  if (close !== null) {
    markers.push({ key: 'regular-close', label: 'REGULAR CLOSE', value: close, tone: 'close', near: false, distancePercent: 0, lane: 2 })
  }
  if (spot !== null && (close === null || Math.abs(spot - close) > 0.0001)) {
    markers.push({ key: 'option-spot', label: 'OPTION SPOT', value: spot, tone: 'spot', near: false, distancePercent: distancePercent(spot, comparisonPrice.value), lane: 2 })
  }
  return markers
})

const visualMarkers = computed(() => [...optionLevelMarkers.value, ...priceMarkers.value])

const expectedMoveRange = computed<{ lower: number; upper: number; amount: number; percent: number | null } | null>(() => {
  const expiry = currentExpiry.value
  const center = finiteNumber(currentSymbol.value?.spot) ?? comparisonPrice.value
  if (!expiry || center === null) return null
  const expectedPercent = finiteNumber(expiry.expected_move.percent)
  const amount = finiteNumber(expiry.expected_move.amount)
    ?? (expectedPercent === null ? null : center * expectedPercent / 100)
  if (amount === null) return null
  const percent = expectedPercent ?? (center === 0 ? null : amount / Math.abs(center) * 100)
  return { lower: center - amount, upper: center + amount, amount, percent }
})

const levelMapDomain = computed(() => {
  const values = [
    ...visualMarkers.value.map((marker) => marker.value),
    expectedMoveRange.value?.lower,
    expectedMoveRange.value?.upper,
  ].filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
  if (!values.length) return { min: 0, max: 1 }
  const min = Math.min(...values)
  const max = Math.max(...values)
  const center = comparisonPrice.value ?? max
  const spread = Math.max(max - min, Math.abs(center) * 0.02, 1)
  const padding = Math.max(spread * 0.14, Math.abs(center) * 0.01, 0.5)
  return { min: min - padding, max: max + padding }
})

function markerPosition(value: number): number {
  const { min, max } = levelMapDomain.value
  if (max === min) return 50
  return Math.min(98, Math.max(2, (value - min) / (max - min) * 100))
}

const expectedBandStyle = computed(() => {
  const range = expectedMoveRange.value
  if (!range) return {}
  const left = markerPosition(range.lower)
  const right = markerPosition(range.upper)
  return { left: `${left}%`, width: `${Math.max(2, right - left)}%` }
})

const axisLabels = computed(() => {
  const { min, max } = levelMapDomain.value
  return [min, max]
})

const nearestLevel = computed<{ marker: LevelMarker; distance: number | null } | null>(() => {
  const price = comparisonPrice.value
  if (price === null || !optionLevelMarkers.value.length) return null
  return optionLevelMarkers.value
    .map((marker) => ({ marker, distance: marker.distancePercent ?? distancePercent(marker.value, price) }))
    .sort((left, right) => (left.distance ?? Infinity) - (right.distance ?? Infinity))[0] ?? null
})

const nearestLevelLabel = computed(() => {
  const value = nearestLevel.value
  return value ? `${value.marker.label} · ${formatNumber(value.marker.value)}` : '不可用'
})

const nearestLevelDistance = computed(() => {
  const value = nearestLevel.value?.distance
  return value === null || value === undefined ? '距离不可用' : `${value.toFixed(2)}%`
})

const expectedMoveLabel = computed(() => {
  const range = expectedMoveRange.value
  if (!range) return '不可用'
  return `±${formatNumber(range.amount)}${range.percent === null ? '' : ` · ±${formatNumber(range.percent)}%`}`
})

const expectedMoveRangeLabel = computed(() => {
  const range = expectedMoveRange.value
  return range ? `${formatNumber(range.lower)} — ${formatNumber(range.upper)}` : '不可用'
})

const expectedMoveCenterLabel = computed(() => finiteNumber(currentSymbol.value?.spot) !== null
  ? 'OPTION SPOT center'
  : 'reference price center')

const levelMapAriaLabel = computed(() => {
  const markerText = visualMarkers.value.map((marker) => `${marker.label} ${formatNumber(marker.value)}`).join('，')
  return `${props.symbol} ${currentExpiry.value?.expiration ?? ''} 期权关键价位：${markerText}`
})

const sourceLabel = computed(() => {
  const phase = props.sourceRunType ? runTypeLabel(props.sourceRunType) : '最近运行快照'
  return props.sourceCutoff ? `${phase} · ${formatDate(props.sourceCutoff)}` : phase
})

function signedCompact(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '不可用'
  return `${value >= 0 ? '+' : ''}${new Intl.NumberFormat('zh-CN', { notation: 'compact', maximumFractionDigits: 2 }).format(value)}`
}
</script>

<template>
  <section class="instrument-options-evidence" data-testid="instrument-options-evidence">
    <div class="instrument-options-heading">
      <div>
        <span class="section-kicker">OPTIONS / STAGE 2</span>
        <h2>{{ symbol }} 期权位置</h2>
        <p>用官方收盘价判断它是否靠近 Max Pain 或 DEX Wall；收盘价缺失时仅显示 OPTION SPOT 参考位置，不作回看结论。</p>
      </div>
      <div class="instrument-options-status-stack">
        <span class="instrument-options-status" :data-status="statusTone">{{ statusLabel }}</span>
        <span class="instrument-options-alignment-status" :data-status="alignmentTone">{{ alignmentLabel }}</span>
      </div>
    </div>

    <div v-if="loading" class="instrument-options-empty">
      <strong>正在读取最近的期权快照…</strong>
      <span>优先读取收盘后复盘数据，等待快照完成后再显示结构标记。</span>
    </div>
    <div v-else-if="error" class="instrument-options-empty instrument-options-error">
      <strong>期权快照读取失败</strong>
      <span>{{ error }}</span>
    </div>
    <template v-else>
      <div v-if="liveOptions && currentSymbol" class="instrument-options-live">
        <div class="instrument-options-source">
          <span>来源：{{ sourceLabel }}</span>
          <span>{{ currentSymbol.expirations.length }} 个到期日 · {{ liveOptions.provider }}</span>
          <span>回看阈值 ±{{ formatNumber(proximityPercent) }}%</span>
        </div>

        <div v-if="currentSymbol.expirations.length" class="instrument-options-expirations">
          <span class="instrument-options-label">EXPIRATIONS</span>
          <button
            v-for="expiry in currentSymbol.expirations"
            :key="expiry.expiration"
            type="button"
            :class="{ active: expiry.expiration === currentExpiry?.expiration }"
            @click="selectedExpiration = expiry.expiration"
          >
            <strong>{{ expiry.expiration }}</strong><small>{{ expiry.days_to_expiry }} DTE</small>
          </button>
        </div>

        <div class="instrument-options-decision-grid">
          <div class="instrument-options-decision-price">
            <span>{{ comparisonPriceLabel }}</span>
            <strong>{{ formatNumber(comparisonPrice) }}</strong>
            <small>{{ comparisonPriceKind }}</small>
          </div>
          <div class="instrument-options-decision-verdict" :data-status="alignmentTone">
            <span>POST-CLOSE CHECK</span>
            <strong>{{ alignmentLabel }}</strong>
            <small>{{ alignmentDetail }}</small>
          </div>
          <div class="instrument-options-decision-detail">
            <span>NEAREST LEVEL</span>
            <strong>{{ nearestLevelLabel }}</strong>
            <small>{{ comparisonDistanceLabel }} {{ nearestLevelDistance }}</small>
          </div>
          <div class="instrument-options-decision-detail">
            <span>EXPIRY</span>
            <strong>{{ currentExpiry?.expiration ?? '不可用' }}</strong>
            <small>{{ currentExpiry?.days_to_expiry ?? '—' }} DTE</small>
          </div>
        </div>

        <div class="instrument-options-level-map" role="img" :aria-label="levelMapAriaLabel">
          <div class="instrument-options-level-map-head">
            <span>PRICE LOCATION</span>
            <span>{{ currentExpiry?.expiration ?? '—' }} · {{ mapComparisonLabel }}</span>
          </div>
          <div class="instrument-options-level-track">
            <div v-if="expectedMoveRange" class="instrument-options-expected-band" :style="expectedBandStyle">
              <span class="instrument-options-expected-band-title">EXPECTED MOVE · {{ expectedMoveRangeLabel }}</span>
            </div>
            <div
              v-for="marker in visualMarkers"
              :key="marker.key"
              class="instrument-options-level-marker"
              :class="{ 'instrument-options-price-marker': marker.tone === 'close' || marker.tone === 'spot' }"
              :data-tone="marker.tone"
              :data-near="marker.near ? 'true' : 'false'"
              :style="{ left: `${markerPosition(marker.value)}%`, top: `${marker.lane * 28 + 3}px` }"
            >
              <span>{{ marker.label }}</span>
              <strong>{{ formatNumber(marker.value) }}</strong>
              <i></i>
            </div>
          </div>
          <div class="instrument-options-level-axis">
            <span v-for="value in axisLabels" :key="value">{{ formatNumber(value) }}</span>
          </div>
          <div class="instrument-options-level-legend">
            <span><i :data-tone="usesRegularClose ? 'close' : 'spot'"></i>{{ referenceLegendLabel }}</span>
            <span><i data-tone="max-pain"></i>Max Pain</span>
            <span><i data-tone="call-dex"></i><i data-tone="put-dex"></i><i data-tone="net-dex"></i>DEX Walls</span>
            <span v-if="expectedMoveRange"><i data-tone="expected"></i>Expected Move {{ expectedMoveRangeLabel }}</span>
          </div>
        </div>

        <div class="instrument-options-support">
          <div>
            <span>EXPECTED RANGE</span>
            <strong>{{ expectedMoveRangeLabel }}</strong>
            <small>{{ expectedMoveLabel }} · {{ expectedMoveCenterLabel }}</small>
          </div>
          <div>
            <span>NET DEX</span>
            <strong>{{ signedCompact(currentExpiry?.exposure.totals.net_dex) }}</strong>
            <small>当前到期日</small>
          </div>
          <div>
            <span>MODELED NET GEX</span>
            <strong>{{ signedCompact(currentExpiry?.exposure.totals.modeled_net_gex) }}</strong>
            <small>{{ currentExpiry?.days_to_expiry ?? '—' }} DTE</small>
          </div>
        </div>

        <div v-if="alignmentFlags.length" class="instrument-options-flag-row">
          <span class="instrument-options-label">MARKERS</span>
          <span v-for="flag in alignmentFlags" :key="flag" class="instrument-options-flag" :data-flag="flag">{{ flagLabel(flag) }}</span>
        </div>
      </div>

      <div v-else-if="liveOptions" class="instrument-options-empty">
        <strong>{{ symbol }} 未进入本轮期权采集目标</strong>
        <span>期权快照已返回其他标的；当前个股仍保留在股票证据页中。</span>
      </div>
      <div v-else-if="props.options?.is_mock" class="instrument-options-empty">
        <strong>期权结构尚未采集</strong>
        <span>当前运行只提供占位状态，页面不会把模拟数据显示成真实期权事实。</span>
      </div>
      <div v-else class="instrument-options-empty">
        <strong>暂无可用期权快照</strong>
        <span>等待 Stage 2 期权采集完成后，这里会随当前股票显示。</span>
      </div>
    </template>
  </section>
</template>
