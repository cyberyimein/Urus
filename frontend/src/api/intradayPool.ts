import { request } from '@/api/client'
import type { IntradayPoolSettings } from '@/types/intradayPool'

export const intradayPoolApi = {
  get: () => request<IntradayPoolSettings>('/settings/intraday-pool'),
  save: (baseRevision: number, selectedSymbols: string[]) =>
    request<IntradayPoolSettings>('/settings/intraday-pool', {
      method: 'PUT',
      body: JSON.stringify({ base_revision: baseRevision, selected_symbols: selectedSymbols }),
    }),
}
