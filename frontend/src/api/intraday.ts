import { request } from '@/api/client'
import type { IntradayProjection, IntradaySymbolDetail } from '@/types/intraday'
import type { IntradayEventHealth, IntradayEventsResponse } from '@/types/intradayEvents'

export interface IntradayEventFilters {
  afterId?: number
  symbol?: string
  strategy?: string
  status?: string
  limit?: number
}

export function getIntradayOverview(signal?: AbortSignal): Promise<IntradayProjection> {
  return request<IntradayProjection>('/intraday/overview', { signal })
}

export function getIntradaySymbol(
  symbol: string,
  signal?: AbortSignal,
): Promise<IntradaySymbolDetail> {
  return request<IntradaySymbolDetail>(
    `/intraday/symbols/${encodeURIComponent(symbol.trim().toUpperCase())}`,
    { signal },
  )
}

export function getIntradayEvents(
  filters: IntradayEventFilters = {},
  signal?: AbortSignal,
): Promise<IntradayEventsResponse> {
  const query = new URLSearchParams()
  if (filters.afterId !== undefined) query.set('after_id', String(filters.afterId))
  if (filters.symbol?.trim()) query.set('symbol', filters.symbol.trim().toUpperCase())
  if (filters.strategy) query.set('strategy', filters.strategy)
  if (filters.status) query.set('status', filters.status)
  if (filters.limit !== undefined) query.set('limit', String(filters.limit))
  const queryString = query.toString()
  const suffix = queryString ? `?${queryString}` : ''
  return request<IntradayEventsResponse>(`/intraday/events${suffix}`, { signal })
}

export function getIntradayEventHealth(signal?: AbortSignal): Promise<IntradayEventHealth> {
  return request<IntradayEventHealth>('/intraday/health', { signal })
}
