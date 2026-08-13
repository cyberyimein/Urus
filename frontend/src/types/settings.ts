export interface ScheduleSlotSettings {
  enabled: boolean
  skip_ai_decision: boolean
}

export interface ScheduleSettings {
  pre_market: ScheduleSlotSettings
  pre_close: ScheduleSlotSettings
  post_close_review: ScheduleSlotSettings
}

export interface RuntimeModelSettings {
  ai_decision_model: string
  anomalo_retrieval_agent: string
}

export interface RuntimeSettingsUpdate {
  revision: number
  schedule: ScheduleSettings
  models: RuntimeModelSettings
}

export interface RuntimeSettingsResponse extends RuntimeSettingsUpdate {
  source: 'environment' | 'runtime'
  updated_at: string | null
  notes: {
    anomalo_model_control: 'preset_agent'
    anomalo_model_note: string
    credentials_note: string
  }
  capabilities: {
    ai_decision_enabled: boolean
    openrouter_configured: boolean
    provider: 'openrouter'
  }
}
