export const MARKET_TIME_ZONE = 'America/New_York'

export type MarketSession = 'pre_market' | 'regular' | 'after_hours' | 'closed'

export interface MarketClockState {
  easternTime: string
  easternDate: string
  headline: string
  session: MarketSession
  sessionLabel: string
  sessionDetail: string
  countdownLabel: string
  countdown: string
  targetLabel: string
  progress: number
  holiday: string | null
}

interface EasternParts {
  year: number
  month: number
  day: number
  hour: number
  minute: number
  second: number
  weekday: number
}

interface CalendarDate {
  year: number
  month: number
  day: number
}

const easternPartsFormatter = new Intl.DateTimeFormat('en-US', {
  timeZone: MARKET_TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
})

const easternTimeFormatter = new Intl.DateTimeFormat('en-US', {
  timeZone: MARKET_TIME_ZONE,
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
})

const easternDateFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: MARKET_TIME_ZONE,
  month: 'long',
  day: 'numeric',
  weekday: 'short',
})

function partsToObject(parts: Intl.DateTimeFormatPart[]): Record<string, string> {
  return Object.fromEntries(parts.map((part) => [part.type, part.value]))
}

function easternParts(value: Date): EasternParts {
  const values = partsToObject(easternPartsFormatter.formatToParts(value))
  const year = Number(values.year)
  const month = Number(values.month)
  const day = Number(values.day)
  return {
    year,
    month,
    day,
    hour: Number(values.hour),
    minute: Number(values.minute),
    second: Number(values.second),
    weekday: new Date(Date.UTC(year, month - 1, day)).getUTCDay(),
  }
}

function dateKey(value: CalendarDate): string {
  return `${value.year}-${String(value.month).padStart(2, '0')}-${String(value.day).padStart(2, '0')}`
}

function calendarFromUtc(value: Date): CalendarDate {
  return { year: value.getUTCFullYear(), month: value.getUTCMonth() + 1, day: value.getUTCDate() }
}

function addCalendarDays(value: CalendarDate, amount: number): CalendarDate {
  const date = new Date(Date.UTC(value.year, value.month - 1, value.day + amount))
  return calendarFromUtc(date)
}

function observedFixedDate(year: number, month: number, day: number): CalendarDate {
  const date = new Date(Date.UTC(year, month - 1, day))
  const weekday = date.getUTCDay()
  if (weekday === 6) return calendarFromUtc(new Date(Date.UTC(year, month - 1, day - 1)))
  if (weekday === 0) return calendarFromUtc(new Date(Date.UTC(year, month - 1, day + 1)))
  return { year, month, day }
}

function nthWeekday(year: number, month: number, weekday: number, occurrence: number): CalendarDate {
  const first = new Date(Date.UTC(year, month - 1, 1))
  const offset = (weekday - first.getUTCDay() + 7) % 7
  return calendarFromUtc(new Date(Date.UTC(year, month - 1, 1 + offset + (occurrence - 1) * 7)))
}

function lastWeekday(year: number, month: number, weekday: number): CalendarDate {
  const last = new Date(Date.UTC(year, month, 0))
  const offset = (last.getUTCDay() - weekday + 7) % 7
  return calendarFromUtc(new Date(Date.UTC(year, month, 0 - offset)))
}

function easterSunday(year: number): CalendarDate {
  const a = year % 19
  const b = Math.floor(year / 100)
  const c = year % 100
  const d = Math.floor(b / 4)
  const e = b % 4
  const f = Math.floor((b + 8) / 25)
  const g = Math.floor((b - f + 1) / 3)
  const h = (19 * a + b - d - g + 15) % 30
  const i = Math.floor(c / 4)
  const k = c % 4
  const l = (32 + 2 * e + 2 * i - h - k) % 7
  const m = Math.floor((a + 11 * h + 22 * l) / 451)
  const month = Math.floor((h + l - 7 * m + 114) / 31)
  const day = ((h + l - 7 * m + 114) % 31) + 1
  return { year, month, day }
}

function holidaysForYear(year: number): Array<{ date: CalendarDate; name: string }> {
  const easter = easterSunday(year)
  return [
    { date: observedFixedDate(year, 1, 1), name: '元旦休市' },
    { date: nthWeekday(year, 1, 1, 3), name: '马丁·路德·金纪念日' },
    { date: nthWeekday(year, 2, 1, 3), name: '总统日' },
    { date: addCalendarDays(easter, -2), name: '耶稣受难日' },
    { date: lastWeekday(year, 5, 1), name: '阵亡将士纪念日' },
    { date: observedFixedDate(year, 6, 19), name: '六月节' },
    { date: observedFixedDate(year, 7, 4), name: '独立日' },
    { date: nthWeekday(year, 9, 1, 1), name: '劳动节' },
    { date: nthWeekday(year, 11, 4, 4), name: '感恩节' },
    { date: observedFixedDate(year, 12, 25), name: '圣诞节' },
  ]
}

function marketHoliday(date: CalendarDate): string | null {
  for (const year of [date.year - 1, date.year, date.year + 1]) {
    const match = holidaysForYear(year).find((item) => dateKey(item.date) === dateKey(date))
    if (match) return match.name
  }
  return null
}

function zonedTimeToUtc(date: CalendarDate, hour: number, minute = 0, second = 0): Date {
  const desired = Date.UTC(date.year, date.month - 1, date.day, hour, minute, second)
  let guess = desired
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const local = easternParts(new Date(guess))
    const localAsUtc = Date.UTC(local.year, local.month - 1, local.day, local.hour, local.minute, local.second)
    guess += desired - localAsUtc
  }
  return new Date(guess)
}

function nextTradingDate(start: CalendarDate): CalendarDate {
  let candidate = addCalendarDays(start, 1)
  for (;;) {
    const weekday = new Date(Date.UTC(candidate.year, candidate.month - 1, candidate.day)).getUTCDay()
    if (weekday !== 0 && weekday !== 6 && !marketHoliday(candidate)) return candidate
    candidate = addCalendarDays(candidate, 1)
  }
}

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

function formatCountdown(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.ceil(milliseconds / 1000))
  const days = Math.floor(totalSeconds / 86400)
  const hours = Math.floor((totalSeconds % 86400) / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  if (days > 0) return `${days}天 ${pad(hours)}:${pad(minutes)}`
  return `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value))
}

export function buildMarketClock(now: Date): MarketClockState {
  const local = easternParts(now)
  const calendar = { year: local.year, month: local.month, day: local.day }
  const holiday = marketHoliday(calendar)
  const weekend = local.weekday === 0 || local.weekday === 6
  const open = zonedTimeToUtc(calendar, 9, 30)
  const close = zonedTimeToUtc(calendar, 16)
  let session: MarketSession
  if (weekend || holiday) session = 'closed'
  else if (now < open) session = 'pre_market'
  else if (now < close) session = 'regular'
  else session = 'after_hours'

  let target: Date
  let countdownLabel: string
  let targetLabel: string
  let progress = 0
  if (session === 'regular') {
    target = close
    countdownLabel = '距离收盘'
    targetLabel = '收盘 16:00 ET'
    progress = clamp((now.getTime() - open.getTime()) / (close.getTime() - open.getTime()) * 100, 0, 100)
  } else if (session === 'pre_market') {
    target = open
    countdownLabel = '距离开盘'
    targetLabel = '开盘 09:30 ET'
  } else {
    const next = nextTradingDate(calendar)
    target = zonedTimeToUtc(next, 9, 30)
    countdownLabel = '距离开盘'
    targetLabel = '下次开盘 09:30 ET'
  }

  const sessionLabel: Record<MarketSession, string> = {
    pre_market: '盘前',
    regular: '交易中',
    after_hours: '盘后',
    closed: '休市',
  }
  const sessionDetail = holiday ?? (weekend ? '周末休市' : session === 'after_hours' ? '常规交易已结束' : session === 'pre_market' ? '等待常规交易开始' : 'NYSE / NASDAQ 常规交易')
  const easternTime = easternTimeFormatter.format(now)
  const easternDate = easternDateFormatter.format(now)
  return {
    easternTime,
    easternDate,
    headline: `美东${sessionLabel[session]} · ${easternTime.slice(0, 5)} ET`,
    session,
    sessionLabel: sessionLabel[session],
    sessionDetail,
    countdownLabel,
    countdown: formatCountdown(target.getTime() - now.getTime()),
    targetLabel,
    progress: session === 'after_hours' ? 100 : Number(progress.toFixed(1)),
    holiday,
  }
}
