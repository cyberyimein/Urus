export interface PostCloseOptionDexWallAlignment {
  kind: string
  label: string
  strike: number
  exposure: number | null
  distance: number | null
  distance_percent: number | null
  near: boolean
}

export interface PostCloseOptionExpirationAlignment {
  expiration: string
  max_pain: number | null
  max_pain_distance: number | null
  max_pain_distance_percent: number | null
  near_max_pain: boolean
  dex_walls: PostCloseOptionDexWallAlignment[]
  near_dex_wall: boolean
  dex_influence_candidate: boolean
  flags: string[]
}

export interface PostCloseOptionSymbolAlignment {
  symbol: string
  status: string
  close_price: number | null
  close_time: string | null
  price_source: string | null
  price_kind?: string | null
  spot: number | null
  flags: string[]
  flagged: boolean
  expirations: PostCloseOptionExpirationAlignment[]
}

export interface PostCloseOptionAlignment {
  available: boolean
  status: string
  source_phase: string
  method: string
  proximity_percent: number
  price_definition: string
  causality_note: string
  symbols: PostCloseOptionSymbolAlignment[]
  flagged_symbols: string[]
  flag_count: number
  unavailable_symbols: string[]
  warnings: string[]
}
