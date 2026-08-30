export function nullable(value: unknown, fallback = '不可用'): string {
  if (value === null || value === undefined || value === '') return fallback
  return String(value)
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '不可用'
  const raw = value.trim()
  const hasOffset = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(raw)
  const iso = raw.includes('T') ? raw : raw.replace(' ', 'T')
  const date = new Date(hasOffset ? iso : `${iso}Z`)
  if (Number.isNaN(date.getTime())) return '不可用'
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Tokyo',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(date)
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]))
  return `${values.month}/${values.day} ${values.hour}:${values.minute} JST`
}

export function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return '不可用'
  return value.toFixed(digits)
}

export function runTypeLabel(value: string): string {
  if (value === 'manual_analysis') return '手动即时分析'
  if (value === 'post_close_review') return '收盘后复盘'
  if (value === 'observation_run') return '盘后观察'
  return value === 'pre_close' ? '收盘前一小时' : '盘前'
}

export function statusLabel(value: string): string {
  const labels: Record<string, string> = {
    pending: '等待中',
    running: '运行中',
    succeeded: '成功',
    live: '真实',
    mixed: '混合',
    placeholder: '占位',
    ok: '正常',
    mock: '模拟',
    degraded: '降级',
    unavailable: '不可用',
    skipped: '已跳过',
    partial: '部分完成',
    failed: '失败',
  }
  return labels[value] ?? value
}

export function dataStateLabel(value: string): string {
  const labels: Record<string, string> = {
    live: '真实数据',
    mock: '模拟数据',
    mixed: '混合数据',
    placeholder: '占位数据',
    unavailable: '不可用',
    skipped: '未执行',
  }
  return labels[value] ?? value
}
