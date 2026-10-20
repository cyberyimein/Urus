import { request } from '@/api/client'
import type { SystematicRiskOverview } from '@/types/systematicRisk'

export function getSystematicRiskOverview(signal?: AbortSignal): Promise<SystematicRiskOverview> {
  return request<SystematicRiskOverview>('/systematic-risk/overview', { signal })
}
