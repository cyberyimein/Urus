<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import AppShell from '@/components/AppShell.vue'
import MockBadge from '@/components/MockBadge.vue'
import OptionsPanel from '@/components/OptionsPanel.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import StepTimeline from '@/components/StepTimeline.vue'
import { useUrusStore } from '@/stores/urus'
import type { EventSummary, MarketCard, RunType } from '@/types/api'
import { formatDate, formatNumber, nullable, runTypeLabel } from '@/utils/format'

type TabId = 'market' | 'instrument' | 'events' | 'options' | 'decision' | 'quality'

interface DashboardTab {
  id: TabId
  label: string
  meta: string
  status: string
}

interface MacroObservation {
  value?: number
  as_of?: string
  source?: string
  label?: string
  unit?: string
}

const store = useUrusStore()
const route = useRoute()
const activeTab = ref<TabId>(route.query.tab === 'options' ? 'options' : 'market')
const runType = ref<RunType>('pre_market')
const simulateMacroEvent = ref(false)
const simulateInstrumentEvent = ref(false)
const runSteps = computed(() => store.latestRun?.steps ?? [])
const readModel = computed(() => store.latestReadModel)
const market = computed<MarketCard | null>(() => readModel.value?.market ?? null)

const macroCards = [
  { key: 'vix', label: 'VIX', unit: '点' },
  { key: 'us_2y_yield', label: '美国 2Y', unit: '%' },
  { key: 'us_10y_yield', label: '美国 10Y', unit: '%' },
  { key: 'us_30y_yield', label: '美国 30Y', unit: '%' },
  { key: 'us_2s10s_spread', label: '2s10s', unit: '百分点' },
]

const historyReturnCards = [
  { key: '1d', label: '1D' },
  { key: '5d', label: '5D' },
  { key: '20d', label: '20D' },
  { key: '60d', label: '60D' },
  { key: '120d', label: '120D' },
  { key: '252d', label: '252D' },
]

const movingAverageCards = [
  { key: '20d', label: 'MA20' },
  { key: '50d', label: 'MA50' },
  { key: '200d', label: 'MA200' },
]

const tabs = computed<DashboardTab[]>(() => {
  const current = readModel.value
  const snapshot = current?.market?.market_snapshot
  const returnedSnapshotCount = snapshot?.returned_symbols?.length
  const requestedSnapshotCount = snapshot?.requested_symbols?.length
  const eventStatuses = [current?.macro_event.status, current?.instrument_event.status]
  const eventStatus = eventStatuses.includes('failed')
    ? 'failed'
    : eventStatuses.includes('succeeded')
      ? 'succeeded'
      : eventStatuses.includes('skipped')
        ? 'skipped'
        : 'unavailable'

  return [
    {
      id: 'market',
      label: '大盘 / 1A',
      meta:
        returnedSnapshotCount !== undefined && requestedSnapshotCount !== undefined
          ? `${returnedSnapshotCount}/${requestedSnapshotCount} 个快照`
          : '未采集',
      status: current?.market?.quality_status ?? 'unavailable',
    },
    {
      id: 'instrument',
      label: '个股 / 3A',
      meta: current?.instrument?.symbol ?? '未采集',
      status: current?.instrument?.data_state ?? 'unavailable',
    },
    { id: 'events', label: '事件 / 1B + 3B', meta: '宏观 + 个股', status: eventStatus },
    {
      id: 'options',
      label: '期权 / 2',
      meta: current?.options?.available ? '已采集' : '未接入',
      status: current?.options?.data_state ?? 'placeholder',
    },
    {
      id: 'decision',
      label: '决策 / 4',
      meta: current?.decision?.stance ?? '未接入',
      status: current?.decision?.data_state ?? 'placeholder',
    },
    {
      id: 'quality',
      label: '运行 / 5',
      meta: current?.data_quality.status ?? '未运行',
      status: current?.data_quality.status ?? 'unavailable',
    },
  ]
})

const macroContext = computed(() => market.value?.macro_context ?? null)
const macroObservations = computed(() => macroContext.value?.observations ?? {})
const macroDerived = computed(() => macroContext.value?.derived ?? {})

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : null
}

function toNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value))) return Number(value)
  return null
}

function observation(key: string): MacroObservation | null {
  const value = asRecord(macroObservations.value[key])
  return value as MacroObservation | null
}

function derivedObservation(key: string): MacroObservation | null {
  const value = asRecord(macroDerived.value[key])
  return value as MacroObservation | null
}

function macroValue(key: string): number | null {
  return toNumber(observation(key)?.value ?? derivedObservation(key)?.value)
}

function macroAsOf(key: string): string {
  return observation(key)?.as_of ?? derivedObservation(key)?.as_of ?? '不可用'
}

function macroSource(key: string): string {
  return observation(key)?.source ?? derivedObservation(key)?.source ?? macroContext.value?.source ?? '不可用'
}

function historyValue(section: string, key: string): unknown {
  const history = asRecord(market.value?.history)
  return asRecord(history?.[section])?.[key]
}

function historyTop(key: string): unknown {
  return asRecord(market.value?.history)?.[key]
}

function technicalIndicators(): Record<string, unknown> | null {
  return asRecord(asRecord(market.value?.history)?.technical_indicators)
}

function technicalMetric(key: string): Record<string, unknown> | null {
  return asRecord(technicalIndicators()?.[key])
}

function technicalValue(key: string): number | null {
  return toNumber(technicalMetric(key)?.value)
}

function bollingerValue(key: string): number | null {
  return toNumber(technicalMetric('bollinger_20_2')?.[key])
}

function technicalMeta(key: string): string {
  const metric = technicalMetric(key) ?? technicalIndicators()
  if (!metric) return '不可用'
  return `${metric.source || '不可用'} · ${metric.as_of || '不可用'} · n=${metric.sample_count ?? 0}`
}

function displayPercent(value: unknown, digits = 2): string {
  const numeric = toNumber(value)
  if (numeric === null) return '不可用'
  return `${numeric >= 0 ? '+' : ''}${numeric.toFixed(digits)}%`
}

function displayVolume(value: unknown): string {
  const numeric = toNumber(value)
  if (numeric === null) return '不可用'
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 }).format(numeric)
}

function displayQuoteTime(value: string | null | undefined): string {
  return value || '不可用'
}

function quoteChangeClass(value: unknown): string {
  const numeric = toNumber(value)
  if (numeric === null) return ''
  return numeric >= 0 ? 'positive-text' : 'negative-text'
}

function eventText(event: EventSummary): string {
  return event.summary || event.reason || '当前没有摘要。'
}

async function triggerRun() {
  await store.triggerRun(runType.value, {
    simulateMacroEvent: simulateMacroEvent.value,
    simulateInstrumentEvent: simulateInstrumentEvent.value,
  })
}

onMounted(() => {
  void store.loadDashboard()
})
</script>

<template>
  <AppShell />
  <main class="page-shell validation-page">
    <header class="validation-header">
      <div>
        <p class="eyebrow">STAGE 1A + 2 / DATA VALIDATION</p>
        <h1>数据采集验证</h1>
      </div>
      <div class="run-launcher compact-launcher">
        <label class="field-label" for="run-type">运行类型</label>
        <select id="run-type" v-model="runType">
          <option value="pre_market">盘前</option>
          <option value="pre_close">收盘前一小时</option>
        </select>
        <div class="launcher-actions">
          <label class="check-row"><input v-model="simulateMacroEvent" type="checkbox" /><span>模拟宏观事件</span></label>
          <label class="check-row"><input v-model="simulateInstrumentEvent" type="checkbox" /><span>模拟个股事件</span></label>
          <button class="primary-button" :disabled="store.busy" @click="triggerRun">{{ store.busy ? '运行中…' : '开始采集' }}</button>
        </div>
      </div>
    </header>

    <div v-if="store.error" class="error-banner" role="alert">{{ store.error }}</div>

    <section v-if="store.latestRun && store.latestReadModel" class="validation-workspace">
      <div class="connection-strip">
        <div>
          <span class="eyebrow">后端连接</span>
          <strong :data-connection="store.connection">{{ store.connection === 'connected' ? '已连接' : store.connection === 'offline' ? '不可用' : '检查中' }}</strong>
        </div>
        <div class="connection-meta">
          <span>{{ runTypeLabel(store.latestRun.run_type) }}</span>
          <span class="mono">run {{ store.latestRun.id.slice(0, 8) }}</span>
          <span class="mono">snapshot {{ store.latestRun.snapshot_id?.slice(0, 8) || '不可用' }}</span>
        </div>
      </div>

      <div class="run-meta-grid validation-meta">
        <div><span>运行状态</span><strong>{{ store.latestRun.status }}</strong></div>
              <div><span>截止时间（JST）</span><strong>{{ formatDate(store.latestRun.cutoff_time) }}</strong></div>
              <div><span>生成时间（JST）</span><strong>{{ formatDate(store.latestReadModel.generated_at) }}</strong></div>
        <div><span>总体质量</span><strong>{{ store.latestReadModel.data_quality.status }}</strong></div>
      </div>

      <nav class="validation-tabs" aria-label="数据验证模块" role="tablist">
        <button
          v-for="tab in tabs"
          :key="tab.id"
          class="validation-tab"
          :class="{ active: activeTab === tab.id }"
          :aria-selected="activeTab === tab.id"
          role="tab"
          type="button"
          @click="activeTab = tab.id"
        >
          <span class="tab-label">{{ tab.label }}</span>
          <StatusBadge :status="tab.status" />
          <small>{{ tab.meta }}</small>
        </button>
      </nav>

      <section v-if="activeTab === 'market'" class="tab-panel" role="tabpanel">
        <div class="tab-titlebar">
          <div><p class="eyebrow">COLLECTED / 1A</p><h2>大盘数据</h2></div>
          <span v-if="market?.is_mock === false" class="live-badge">Moomoo OpenD</span>
          <MockBadge v-else />
        </div>

        <template v-if="market">
          <section class="data-section">
            <div class="section-label-row"><div><span class="section-kicker">PRIMARY</span><h3>QQQ 当前快照</h3></div><span class="source-label">{{ market.source }}</span></div>
            <div class="metric-grid primary-metrics">
              <div class="metric-cell metric-cell-major"><span>当前价格</span><strong>{{ formatNumber(market.last_price) }}</strong><small>{{ market.session_label || '不可用' }}</small></div>
              <div class="metric-cell"><span>相对昨收</span><strong :class="quoteChangeClass(market.change_percent)">{{ displayPercent(market.change_percent, 4) }}</strong><small>常规：{{ displayPercent(market.regular_change_percent, 4) }}</small></div>
              <div class="metric-cell"><span>昨收</span><strong>{{ formatNumber(market.previous_close) }}</strong><small>报价时间：{{ displayQuoteTime(market.quote_time) }}</small></div>
              <div class="metric-cell"><span>成交量</span><strong>{{ displayVolume(market.volume) }}</strong><small>来源：{{ market.source }}</small></div>
              <div class="metric-cell"><span>盘前</span><strong>{{ formatNumber(market.premarket_price) }}</strong><small>{{ displayVolume(market.premarket_volume) }} · {{ displayPercent(market.premarket_change_percent, 4) }}</small></div>
              <div class="metric-cell"><span>盘后</span><strong>{{ formatNumber(market.afterhours_price) }}</strong><small>{{ displayVolume(market.afterhours_volume) }} · {{ displayPercent(market.afterhours_change_percent, 4) }}</small></div>
            </div>
          </section>

          <section class="data-section">
            <div class="section-label-row"><div><span class="section-kicker">SNAPSHOT UNIVERSE</span><h3>大盘与跨资产代理</h3></div><span class="source-label">{{ market.market_snapshot?.returned_symbols.length ?? 0 }}/{{ market.market_snapshot?.requested_symbols.length ?? 0 }} 返回</span></div>
            <div class="table-wrap">
              <table class="data-table">
                <thead><tr><th>标的</th><th>最新</th><th>变化</th><th>昨收</th><th>开 / 高 / 低</th><th>成交量</th><th>成交额</th><th>买 / 卖</th><th>价差</th><th>盘前 / 盘后</th><th>报价时间</th></tr></thead>
                <tbody>
                  <tr v-for="quote in market.market_snapshot?.quotes ?? []" :key="quote.quote_code || quote.symbol">
                    <td><strong>{{ quote.symbol }}</strong><small>{{ quote.label }}</small></td>
                    <td>{{ formatNumber(quote.last_price) }}</td>
                    <td :class="quoteChangeClass(quote.change_percent)">{{ displayPercent(quote.change_percent, 4) }}</td>
                    <td>{{ formatNumber(quote.previous_close) }}</td>
                    <td>{{ formatNumber(quote.open_price) }} / {{ formatNumber(quote.high_price) }} / {{ formatNumber(quote.low_price) }}</td>
                    <td>{{ displayVolume(quote.volume) }}</td>
                    <td>{{ formatNumber(quote.turnover, 0) }}</td>
                    <td>{{ formatNumber(quote.bid_price) }} / {{ formatNumber(quote.ask_price) }}</td>
                    <td>{{ formatNumber(quote.price_spread, 4) }}</td>
                    <td>{{ formatNumber(quote.premarket_price) }} / {{ formatNumber(quote.afterhours_price) }}</td>
                    <td>{{ displayQuoteTime(quote.quote_time) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-if="market.market_snapshot?.unavailable_symbols.length" class="notice-box warning-box"><strong>未返回标的</strong><span>{{ market.market_snapshot.unavailable_symbols.join('、') }}</span></div>
          </section>

          <section class="data-section">
            <div class="section-label-row"><div><span class="section-kicker">MACRO CONTEXT</span><h3>宏观日频数据</h3></div><span class="source-label">{{ macroContext?.source || '不可用' }} · {{ macroContext?.quality_status || '不可用' }}</span></div>
            <div class="metric-grid macro-metrics">
              <div v-for="item in macroCards" :key="item.key" class="metric-cell"><span>{{ item.label }}</span><strong>{{ formatNumber(macroValue(item.key)) }} {{ item.unit }}</strong><small>{{ macroSource(item.key) }} · {{ macroAsOf(item.key) }}</small></div>
            </div>
            <div v-if="market.market_snapshot?.vix && !market.market_snapshot.vix.available" class="notice-box warning-box"><strong>Moomoo 直接 VIX（已跳过）</strong><span>{{ market.market_snapshot.vix.reason || '按策略不请求美国指数' }}。上方 VIX 按 Yahoo/FRED 宏观日频源显示。</span></div>
            <div v-if="macroContext?.quality_warnings.length" class="notice-box"><strong>宏观数据提示</strong><span>{{ macroContext.quality_warnings.join('；') }}</span></div>
          </section>

          <section class="data-section">
            <div class="section-label-row"><div><span class="section-kicker">HISTORY SUMMARY</span><h3>QQQ 日线摘要</h3></div><span class="source-label">{{ historyTop('returned_days') || 0 }} / {{ historyTop('requested_days') || 0 }} 根返回</span></div>
            <div class="metric-grid history-metrics">
              <div v-for="item in historyReturnCards" :key="item.key" class="metric-cell"><span>{{ item.label }} 收益</span><strong>{{ displayPercent(historyValue('returns_percent', item.key), 2) }}</strong><small>当前仅有已返回窗口</small></div>
              <div v-for="item in movingAverageCards" :key="item.key" class="metric-cell"><span>{{ item.label }}</span><strong>{{ formatNumber(toNumber(historyValue('moving_average', item.key))) }}</strong><small>复权日线摘要</small></div>
            </div>
          </section>

          <section class="data-section">
            <div class="section-label-row"><div><span class="section-kicker">TECHNICAL INDICATORS</span><h3>QQQ 日线波动与通道</h3></div><span class="source-label">{{ technicalIndicators()?.quality_status || '不可用' }}</span></div>
            <div class="metric-grid history-metrics">
              <div class="metric-cell"><span>20D 年化实现波动率</span><strong>{{ displayPercent(technicalValue('realized_volatility_20d'), 2) }}</strong><small>{{ technicalMeta('realized_volatility_20d') }}</small></div>
              <div class="metric-cell"><span>ATR14</span><strong>{{ formatNumber(technicalValue('atr14'), 4) }}</strong><small>{{ technicalMeta('atr14') }} · 绝对值</small></div>
              <div class="metric-cell"><span>ATR14%</span><strong>{{ displayPercent(technicalValue('atr14_percent'), 2) }}</strong><small>{{ technicalMeta('atr14_percent') }}</small></div>
              <div class="metric-cell"><span>布林上轨 20/2</span><strong>{{ formatNumber(bollingerValue('upper'), 4) }}</strong><small>{{ technicalMeta('bollinger_20_2') }}</small></div>
              <div class="metric-cell"><span>布林中轨 20/2</span><strong>{{ formatNumber(bollingerValue('middle'), 4) }}</strong><small>{{ technicalMeta('bollinger_20_2') }}</small></div>
              <div class="metric-cell"><span>布林下轨 20/2</span><strong>{{ formatNumber(bollingerValue('lower'), 4) }}</strong><small>{{ technicalMeta('bollinger_20_2') }}</small></div>
              <div class="metric-cell"><span>布林当前位置</span><strong>{{ displayPercent(bollingerValue('position_percent'), 2) }}</strong><small>当前价 {{ formatNumber(bollingerValue('current_price'), 4) }} · {{ technicalMeta('bollinger_20_2') }}</small></div>
            </div>
          </section>

          <section class="data-section unfinished-section">
            <div class="section-label-row"><div><span class="section-kicker">NOT COLLECTED</span><h3>当前未接入</h3></div></div>
            <div class="unfinished-list"><span>5年日线原始归档</span><span>市场涨跌家数</span><span>行业热力图</span><span>60/120/252日收益</span><span>5分钟 OHLCV</span><span>交易日历与提前收盘（自动调度前补）</span><span>相对强弱（延期至 3A）</span></div>
          </section>
        </template>
        <div v-else class="empty-panel"><h3>大盘数据不可用</h3><p>1A 步骤没有返回市场数据。</p></div>
      </section>

      <section v-else-if="activeTab === 'instrument'" class="tab-panel" role="tabpanel">
        <div class="tab-titlebar"><div><p class="eyebrow">COLLECTED / 3A</p><h2>个股数据</h2></div><StatusBadge :status="store.latestReadModel.instrument?.data_state ?? 'unavailable'" /></div>
        <template v-if="store.latestReadModel.instrument">
          <section class="data-section">
            <div class="section-label-row"><div><span class="section-kicker">CURRENT OBJECT</span><h3>{{ store.latestReadModel.instrument.symbol }}</h3></div><span class="source-label">{{ store.latestReadModel.instrument.label }}</span></div>
            <div class="metric-grid primary-metrics"><div class="metric-cell metric-cell-major"><span>当前价格</span><strong>{{ formatNumber(store.latestReadModel.instrument.last_price) }}</strong><small>状态：{{ store.latestReadModel.instrument.data_state }}</small></div><div class="metric-cell"><span>变化</span><strong>{{ displayPercent(store.latestReadModel.instrument.change_percent) }}</strong><small>状态：{{ store.latestReadModel.instrument.data_state }}</small></div><div class="metric-cell"><span>趋势</span><strong>{{ store.latestReadModel.instrument.trend || '不可用' }}</strong><small>未计算</small></div></div>
          </section>
          <section class="data-section"><div class="section-label-row"><div><span class="section-kicker">FIELD STATUS</span><h3>字段状态</h3></div></div><div class="status-list"><div><span>行情快照</span><StatusBadge status="unavailable" /><small>3A 尚未接入真实个股行情</small></div><div><span>技术指标</span><StatusBadge status="unavailable" /><small>{{ store.latestReadModel.instrument.technical_note || '尚未实现' }}</small></div><div><span>事件与财务</span><StatusBadge status="unavailable" /><small>{{ store.latestReadModel.instrument.note }}</small></div></div></section>
        </template>
        <div v-else class="empty-panel"><h3>个股数据不可用</h3><p>3A 步骤没有返回个股数据。</p></div>
      </section>

      <section v-else-if="activeTab === 'events'" class="tab-panel" role="tabpanel">
        <div class="tab-titlebar"><div><p class="eyebrow">CONDITIONAL / 1B + 3B</p><h2>事件数据</h2></div><span class="source-label">只在命中条件时运行</span></div>
        <div class="event-tab-grid">
          <section v-for="event in [store.latestReadModel.macro_event, store.latestReadModel.instrument_event]" :key="event.category" class="event-panel">
            <div class="section-label-row"><div><span class="section-kicker">{{ event.category === 'macro' ? '1B' : '3B' }}</span><h3>{{ event.category === 'macro' ? '宏观事件' : '个股事件' }}</h3></div><StatusBadge :status="event.status" /></div>
            <dl class="field-list"><div><dt>状态</dt><dd>{{ event.status }}</dd></div><div><dt>标题</dt><dd>{{ event.title || '不可用' }}</dd></div><div><dt>摘要</dt><dd>{{ eventText(event) }}</dd></div></dl>
          </section>
        </div>
      </section>

      <OptionsPanel v-else-if="activeTab === 'options'" :options="store.latestReadModel.options" />

      <section v-else-if="activeTab === 'decision'" class="tab-panel" role="tabpanel">
        <div class="tab-titlebar"><div><p class="eyebrow">NOT CONNECTED / 4</p><h2>决策输出</h2></div><StatusBadge :status="store.latestReadModel.decision.data_state" /></div>
        <section class="data-section"><div class="metric-grid primary-metrics"><div class="metric-cell"><span>状态</span><strong>{{ store.latestReadModel.decision.status }}</strong><small>当前为占位结果</small></div><div class="metric-cell"><span>姿态</span><strong>{{ store.latestReadModel.decision.stance || '不可用' }}</strong><small>未调用决策 AI</small></div><div class="metric-cell"><span>置信度</span><strong>{{ store.latestReadModel.decision.confidence === null ? '不可用' : `${(store.latestReadModel.decision.confidence * 100).toFixed(1)}%` }}</strong><small>未计算</small></div></div><div class="notice-box"><strong>当前记录</strong><span>{{ store.latestReadModel.decision.summary }}</span><span>{{ store.latestReadModel.decision.note }}</span></div></section>
      </section>

      <section v-else class="tab-panel" role="tabpanel">
        <div class="tab-titlebar"><div><p class="eyebrow">RUN / 5</p><h2>运行与数据质量</h2></div><StatusBadge :status="store.latestReadModel.data_quality.status" /></div>
        <section class="data-section"><div class="section-label-row"><div><span class="section-kicker">QUALITY SUMMARY</span><h3>{{ store.latestReadModel.data_quality.message }}</h3></div></div><div class="quality-summary"><div><span>状态</span><strong>{{ store.latestReadModel.data_quality.status }}</strong></div><div><span>schema</span><strong>{{ store.latestReadModel.schema_version }}</strong></div><div><span>data mode</span><strong>{{ store.latestReadModel.data_mode }}</strong></div><div><span>snapshot</span><strong class="mono">{{ store.latestReadModel.snapshot_id }}</strong></div></div></section>
        <section class="data-section"><div class="section-label-row"><div><span class="section-kicker">WARNINGS</span><h3>提示与错误</h3></div></div><div v-if="store.latestReadModel.data_quality.warnings.length" class="notice-list"><p v-for="warning in store.latestReadModel.data_quality.warnings" :key="warning" class="notice-box warning-box">{{ warning }}</p></div><p v-else class="empty-state compact">没有质量提示。</p><div v-if="store.latestReadModel.data_quality.errors.length" class="notice-list"><p v-for="error in store.latestReadModel.data_quality.errors" :key="error" class="notice-box danger-box">{{ error }}</p></div></section>
        <section class="data-section"><div class="section-label-row"><div><span class="section-kicker">WORKFLOW</span><h3>步骤状态</h3></div></div><StepTimeline :steps="runSteps" /></section>
        <details class="raw-preview"><summary>查看当前 read model JSON</summary><pre>{{ JSON.stringify(store.latestReadModel, null, 2) }}</pre></details>
      </section>
    </section>

    <section v-else-if="!store.error" class="empty-panel"><p class="eyebrow">NO RUN</p><h2>还没有采集结果。</h2><p>开始一次运行后，这里会按 Tab 展示每个数据模块。</p></section>
  </main>
</template>
