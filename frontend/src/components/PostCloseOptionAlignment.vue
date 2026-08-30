<script setup lang="ts">
import { computed } from 'vue'

import type {
  PostCloseOptionAlignment as PostCloseOptionAlignmentData,
  PostCloseOptionDexWallAlignment,
} from '@/types/research'
import { formatDate, formatNumber } from '@/utils/format'

const props = defineProps<{
  alignment?: PostCloseOptionAlignmentData | null
  focusSymbol?: string
  focusExpiration?: string
  onlySymbol?: string
}>()

interface AlignmentRow {
  symbol: string
  expiration: string
  status: string
  available: boolean
  closePrice: number | null
  closeTime: string | null
  priceKind: string | null | undefined
  maxPain: number | null
  maxPainDistancePercent: number | null
  closestDexWall: PostCloseOptionDexWallAlignment | null
  flags: string[]
  flagged: boolean
}

const alignment = computed(() => props.alignment)
const onlySymbol = computed(() => String(props.onlySymbol ?? '').trim().toUpperCase())
const visibleSymbols = computed(() => {
  const values = alignment.value?.symbols ?? []
  return onlySymbol.value
    ? values.filter((item) => item.symbol.toUpperCase() === onlySymbol.value)
    : values
})
const rows = computed<AlignmentRow[]>(() => {
  return visibleSymbols.value.flatMap((symbol) => (symbol.expirations ?? []).map((expiration) => {
    const walls = expiration.dex_walls ?? []
    const closestDexWall = [...walls]
      .filter((wall) => wall.distance_percent !== null && wall.distance_percent !== undefined)
      .sort((left, right) => (left.distance_percent ?? Infinity) - (right.distance_percent ?? Infinity))[0]
      ?? walls[0]
      ?? null
    return {
      symbol: symbol.symbol,
      expiration: expiration.expiration,
      status: symbol.status,
      available: symbol.status !== 'unavailable' && symbol.close_price !== null,
      closePrice: symbol.close_price,
      closeTime: symbol.close_time,
      priceKind: symbol.price_kind,
      maxPain: expiration.max_pain,
      maxPainDistancePercent: expiration.max_pain_distance_percent,
      closestDexWall,
      flags: expiration.flags ?? [],
      flagged: symbol.status !== 'unavailable' && symbol.close_price !== null && Boolean(expiration.flags?.length),
    }
  }))
})

const unavailableSymbols = computed(() => {
  const values = alignment.value?.unavailable_symbols ?? []
  return onlySymbol.value
    ? values.filter((symbol) => symbol.toUpperCase() === onlySymbol.value)
    : values
})
const flaggedSymbols = computed(() => {
  const values = alignment.value?.flagged_symbols ?? []
  return onlySymbol.value
    ? values.filter((symbol) => symbol.toUpperCase() === onlySymbol.value)
    : values
})
const visibleFlagCount = computed(() => onlySymbol.value
  ? rows.value.filter((row) => row.flagged).length
  : alignment.value?.flag_count ?? 0)
const visibleClearCount = computed(() => rows.value.filter((row) => row.available && !row.flagged).length)
const visibleAvailableSymbolCount = computed(() => visibleSymbols.value.filter((symbol) =>
  symbol.status !== 'unavailable' && symbol.close_price !== null,
).length)
const visibleStatus = computed(() => {
  if (!alignment.value || !onlySymbol.value) return alignment.value?.status
  const symbol = visibleSymbols.value[0]
  if (!symbol || !rows.value.length || !rows.value.some((row) => row.available)) return 'unavailable'
  if (rows.value.some((row) => !row.available)) return 'partial'
  return visibleFlagCount.value ? 'flagged' : 'clear'
})

function alignmentStatusLabel(status: string | undefined): string {
  return ({
    flagged: '命中标记',
    clear: '未命中',
    partial: '部分可用',
    unavailable: '不可用',
  } as Record<string, string>)[status ?? ''] ?? '不可用'
}

function flagLabel(flag: string): string {
  return ({
    near_max_pain: '收盘价接近 Max Pain',
    near_dex_wall: 'DEX 影响候选',
  } as Record<string, string>)[flag] ?? flag
}

function distanceLabel(value: number | null | undefined): string {
  return value === null || value === undefined ? '不可用' : `${value.toFixed(2)}%`
}

function priceKindLabel(value: string | null | undefined): string {
  return value === 'last_price_fallback' ? 'last_price fallback' : value === 'regular_price' ? 'regular close' : '收盘价'
}

function isFocused(row: AlignmentRow): boolean {
  const symbol = String(props.focusSymbol ?? '').toUpperCase()
  const expiration = String(props.focusExpiration ?? '')
  return Boolean(symbol && expiration && row.symbol.toUpperCase() === symbol && row.expiration === expiration)
}

function rowClass(row: AlignmentRow): Record<string, boolean> {
  return {
    'alignment-flagged': row.flagged,
    'alignment-unavailable': !row.available,
    'alignment-selected': isFocused(row),
  }
}

function formatCloseTime(value: string | null): string {
  return value ? formatDate(value) : '时间不可用'
}
</script>

<template>
  <section v-if="alignment" class="post-close-alignment-panel">
    <div class="post-close-alignment-heading">
      <div>
        <p class="eyebrow">POST-CLOSE / OPTIONS ALIGNMENT</p>
        <h3>收盘后期权价位回看</h3>
      </div>
      <span class="post-close-alignment-status" :data-status="visibleStatus">{{ alignmentStatusLabel(visibleStatus) }}</span>
    </div>

    <div class="post-close-alignment-meta">
      <span>官方 regular close · 阈值 ±{{ formatNumber(alignment.proximity_percent) }}%</span>
      <span v-if="visibleFlagCount">{{ visibleFlagCount }} 个到期日命中</span>
      <span v-if="flaggedSymbols.length">标记：{{ flaggedSymbols.join('、') }}</span>
    </div>

    <div v-if="alignment.available && rows.length" class="post-close-alignment-summary">
      <div><span>可对照标的</span><strong>{{ visibleAvailableSymbolCount }}</strong></div>
      <div><span>命中到期日</span><strong>{{ visibleFlagCount }}</strong></div>
      <div><span>未命中到期日</span><strong>{{ visibleClearCount }}</strong></div>
    </div>

    <div v-if="!alignment.available" class="notice-box warning-box post-close-alignment-notice">
      <strong>无法完成收盘对照</strong>
      <span>post-close 没有匹配到可用的官方 regular close，或期权到期日结构为空。</span>
    </div>

    <div v-if="rows.length" class="post-close-alignment-table-wrap">
      <table class="post-close-alignment-table">
        <thead>
          <tr>
            <th>标的 / 到期日</th>
            <th>收盘价</th>
            <th>Max Pain</th>
            <th>MP 距离</th>
            <th>最近 DEX Wall</th>
            <th>DEX 距离</th>
            <th>标记</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="`${row.symbol}-${row.expiration}`" :class="rowClass(row)" :data-status="row.available ? (row.flagged ? 'flagged' : 'clear') : 'unavailable'">
            <td><strong>{{ row.symbol }}</strong><small>{{ row.expiration }}</small></td>
            <td><strong>{{ formatNumber(row.closePrice) }}</strong><small>{{ priceKindLabel(row.priceKind) }} · {{ formatCloseTime(row.closeTime) }}</small></td>
            <td>{{ formatNumber(row.maxPain) }}</td>
            <td>{{ distanceLabel(row.maxPainDistancePercent) }}</td>
            <td>
              <template v-if="row.closestDexWall">
                <strong>{{ formatNumber(row.closestDexWall.strike) }}</strong>
                <small>{{ row.closestDexWall.label }}</small>
              </template>
              <span v-else>不可用</span>
            </td>
            <td>{{ distanceLabel(row.closestDexWall?.distance_percent) }}</td>
            <td>
              <template v-if="row.available">
                <span v-for="flag in row.flags" :key="flag" class="post-close-alignment-badge" :data-flag="flag">{{ flagLabel(flag) }}</span>
                <span v-if="!row.flags.length" class="post-close-alignment-clear">未命中</span>
              </template>
              <span v-else class="post-close-alignment-unavailable">无法判定</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-else class="notice-box warning-box post-close-alignment-notice">
      <strong>没有可展示的期权到期日</strong>
      <span>当前 post-close 快照没有保存可比较的 Max Pain 或 DEX Wall。</span>
    </div>

    <p v-if="unavailableSymbols.length" class="post-close-alignment-warning">未完成对照：{{ unavailableSymbols.join('、') }}</p>
    <p class="post-close-alignment-note">{{ alignment.price_definition }} {{ alignment.causality_note }}</p>
  </section>
</template>
