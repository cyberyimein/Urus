import type { RemoteDecisionRun } from '@/types/remoteDecision'

type DecisionRecord = Record<string, any>

export interface RemoteDecisionMetric {
  label: string
  value: string | number
}

const actionLabels: Record<string, string> = {
  prioritize: '优先关注',
  avoid: '回避',
  watch: '观察',
  wait: '等待确认',
  no_action: '暂不行动',
  select_one: '采信单一策略',
  insufficient_evidence: '证据不足',
}

const stanceLabels: Record<string, string> = {
  bullish: '看多',
  bearish: '看空',
  neutral: '中性',
}

const consensusLabels: Record<string, string> = {
  aligned: '方向一致',
  mixed: '方向混合',
  conflicted: '策略冲突',
  no_signal: '无明确信号',
  insufficient_data: '数据不足',
}

function asRecord(value: unknown): DecisionRecord | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as DecisionRecord : null
}

function displayLabel(value: unknown, labels: Record<string, string>): string | null {
  if (typeof value !== 'string' || !value.trim()) return null
  return labels[value] ?? value
}

function resultDecision(result: RemoteDecisionRun['result']): DecisionRecord | null {
  return asRecord(result?.decision)
}

export function remoteDecisionTitle(result: RemoteDecisionRun['result']): string {
  const summary = typeof result?.summary === 'string' ? result.summary.trim() : ''
  if (summary) return summary

  const decision = resultDecision(result)
  if (!decision) return '结构化决策结果'

  const action = displayLabel(decision.suggested_action ?? decision.action, actionLabels)
  const consensus = displayLabel(decision.consensus_state, consensusLabels)
  const stance = displayLabel(decision.stance, stanceLabels)
  return [action, consensus ?? stance].filter(Boolean).join(' · ') || '结构化决策结果'
}

export function remoteDecisionNarrative(result: RemoteDecisionRun['result']): string | null {
  const decision = resultDecision(result)
  const value = decision?.conflict_summary ?? decision?.summary
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

export function remoteDecisionMetrics(result: RemoteDecisionRun['result']): RemoteDecisionMetric[] {
  const decision = resultDecision(result)
  if (!decision) return []

  const metrics: RemoteDecisionMetric[] = []
  const action = displayLabel(decision.suggested_action ?? decision.action, actionLabels)
  const consensus = displayLabel(decision.consensus_state, consensusLabels)
  const stance = displayLabel(decision.stance, stanceLabels)
  const tradingDate = decision.scope?.trading_date ?? decision.trading_date

  if (action) metrics.push({ label: '建议动作', value: action })
  if (consensus) metrics.push({ label: '共识状态', value: consensus })
  else if (stance) metrics.push({ label: '市场姿态', value: stance })
  if (typeof tradingDate === 'string' && tradingDate) metrics.push({ label: '结论日期', value: tradingDate })

  for (const [key, label] of [
    ['bullish_count', '看多策略'],
    ['bearish_count', '看空策略'],
    ['neutral_count', '中性策略'],
    ['not_applicable_count', '不适用'],
    ['error_count', '错误'],
  ] as const) {
    if (typeof decision[key] === 'number') metrics.push({ label, value: decision[key] })
  }
  return metrics
}

export function remoteDecisionStrategyNames(result: RemoteDecisionRun['result']): string[] {
  const decision = resultDecision(result)
  if (!Array.isArray(decision?.strategy_set)) return []
  return decision.strategy_set
    .map((item: unknown) => {
      const strategy = asRecord(item)
      if (!strategy?.name) return null
      return strategy.version ? `${strategy.name} · ${strategy.version}` : String(strategy.name)
    })
    .filter((value: string | null): value is string => Boolean(value))
}

export function remoteDecisionJson(result: RemoteDecisionRun['result']): string {
  const decision = resultDecision(result)
  return decision ? JSON.stringify(decision, null, 2) : ''
}
