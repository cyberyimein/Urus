export type SystematicRiskStatus = 'disabled' | 'ok' | 'partial' | 'unavailable' | string

export interface SystematicRiskReason {
  code: string
  message: string
}

export interface SystematicRiskModel {
  available: boolean
  reason_code: string | null
  message: string
  model_version: string
  fast_slow: [number, number] | number[]
  parameters: {
    volatility_window: number
    target_vol: number
    cap: number
  }
  baseline: {
    trend_signal: number | null
    annualized_volatility: number | null
    target_exposure: number | null
  }
  scenario: {
    assumed_close: number | null
    trend_signal: number | null
    annualized_volatility: number | null
    target_exposure: number | null
    provisional: boolean
  }
  flip_price: number | null
  flip_price_reason_code?: string | null
  exposure_change: {
    trend_only: number | null
    volatility_only: number | null
    joint: number | null
    interaction: number | null
    direction: string
  }
}

export interface SystematicRiskSymbol {
  status: SystematicRiskStatus
  reasons: SystematicRiskReason[]
  baseline: {
    dataset_id: string | null
    dataset_hash: string | null
    chart_hash: string | null
    input_hash: string | null
    last_bar_date: string | null
    adjustment: string | null
    source: string | null
    baseline_id: string | null
  }
  quote_as_of: string | null
  scenario_id: string | null
  assumed_close: number | null
  provisional: boolean
  models: SystematicRiskModel[]
}

export interface SystematicRiskOverview {
  schema_version: string
  status: SystematicRiskStatus
  evaluated_at: string
  symbols: Record<string, SystematicRiskSymbol>
  reasons?: SystematicRiskReason[]
  warnings?: string[]
}
