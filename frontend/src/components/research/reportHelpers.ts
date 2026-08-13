import { formatNumber } from '@/utils/format'

export type ReportRecord = Record<string, unknown>

export function record(value: unknown): ReportRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as ReportRecord : {}
}

export function records(value: unknown): ReportRecord[] {
  return Array.isArray(value)
    ? value.filter((item): item is ReportRecord => Boolean(item && typeof item === 'object' && !Array.isArray(item)))
    : []
}

export function list(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean) : []
}

export function text(value: unknown, fallback = '—'): string {
  if (value === null || value === undefined || value === '') return fallback
  if (typeof value === 'number') return formatNumber(value)
  return String(value)
}

export function number(value: unknown): number | null {
  const parsed = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

export function evidence(value: unknown): Array<{ path: string; observation: string }> {
  return records(value)
    .filter((item) => typeof item.path === 'string')
    .map((item) => ({ path: String(item.path), observation: text(item.observation, '') }))
}

export function optionFor(options: unknown, symbol: unknown): ReportRecord {
  return records(options).find((item) => String(item.symbol) === String(symbol)) ?? {}
}

export function phaseLabel(value: unknown): string {
  if (value === 'pre_market') return '盘前 · 当日行情预测'
  if (value === 'pre_close') return '尾盘 · 仅采集数据'
  if (value === 'post_close_review') return '收盘 · 当日行情复盘'
  if (value === 'current_state') return '手动 · 当前状态分析'
  return String(value || '阶段未知')
}

export function directionLabel(value: unknown): string {
  return ({
    up: '看涨',
    bullish: '看涨',
    down: '看跌',
    bearish: '看跌',
    flat: '横盘',
    mixed: '分化',
    neutral: '中性',
    unknown: '未知',
  } as Record<string, string>)[String(value)] ?? String(value || '—')
}

export function actionLabel(value: unknown): string {
  return ({
    buy: '买入',
    wait: '等待',
    avoid: '避开',
    cash: '保持现金',
    add: '加仓',
    hold: '持有',
    watch: '关注',
    observe: '观察',
    take_profit: '止盈',
    reduce: '减仓',
    stop_loss: '止损',
    exit: '退出',
  } as Record<string, string>)[String(value)] ?? String(value || '—')
}

export function returnRange(item: ReportRecord): string {
  const range = record(item.expected_return_range_percent)
  return `${text(range.minimum_percent)}% ～ ${text(range.maximum_percent)}%`
}

