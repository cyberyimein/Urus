export function nullable(value: unknown, fallback = '不可用'): string {
  if (value === null || value === undefined || value === '') return fallback
  return String(value)
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '不可用'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '不可用'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(date)
}

export function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return '不可用'
  return value.toFixed(digits)
}

export function runTypeLabel(value: string): string {
  return value === 'pre_close' ? '收盘前一小时' : '盘前'
}

export function statusLabel(value: string): string {
  const labels: Record<string, string> = {
    pending: '等待中',
    running: '运行中',
    succeeded: '成功',
    skipped: '已跳过',
    partial: '部分完成',
    failed: '失败',
    not_implemented: '未实现',
    mock: '模拟',
    error: '错误',
  }
  return labels[value] ?? value
}
