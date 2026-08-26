<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { ApiError, api } from '@/api/client'
import AppShell from '@/components/AppShell.vue'
import type {
  AssetType,
  HistoryCollectionState,
  InstrumentConfig,
  UniverseCapacityPlan,
  UniverseResponse,
} from '@/types/universe'

const universe = ref<UniverseResponse | null>(null)
const items = ref<InstrumentConfig[]>([])
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const query = ref('')
const filter = ref<'all' | AssetType | 'options' | 'cta' | 'ai'>('all')
const listFilter = ref('all')
const selected = ref<InstrumentConfig | null>(null)
const themeDraft = ref('')
const confirming = ref(false)
const pendingRemoval = ref<InstrumentConfig | null>(null)
const capacityPlan = ref<UniverseCapacityPlan | null>(null)
const capacityLoading = ref(false)
const polling = ref(false)
let fingerprint = ''
let pollTimer: ReturnType<typeof setInterval> | undefined

const dirty = computed(() => JSON.stringify(items.value) !== fingerprint)
const watchlistOptions = computed(() => {
  const counts = new Map<string, number>()
  for (const item of items.value) {
    for (const theme of (item.themes?.length ? item.themes : [item.theme])) {
      if (theme) counts.set(theme, (counts.get(theme) ?? 0) + (item.enabled ? 1 : 0))
    }
  }
  return Array.from(counts.entries())
    .map(([name, count]) => ({ name, count }))
    .sort((left, right) => right.count - left.count || left.name.localeCompare(right.name, 'zh-CN'))
})
const filtered = computed(() => items.value.filter((item) => {
  const text = query.value.trim().toUpperCase()
  const matchesText = !text || `${item.symbol} ${item.display_name} ${item.themes?.join(' ') || item.theme}`.toUpperCase().includes(text)
  const matchesList = listFilter.value === 'all'
    ? true
    : listFilter.value === 'core'
      ? item.enabled && item.roles.equity_watchlist
      : (item.themes || [item.theme]).includes(listFilter.value)
  const matchesFilter = filter.value === 'all'
    || item.asset_type === filter.value
    || (filter.value === 'options' && item.collection.options)
    || (filter.value === 'cta' && item.roles.cta_proxy)
    || (filter.value === 'ai' && item.roles.ai_candidate)
  return matchesText && matchesList && matchesFilter
}))
const summary = computed(() => ({
  enabled: items.value.filter((item) => item.enabled).length,
  market: items.value.filter((item) => item.enabled && item.asset_type === 'market').length,
  etf: items.value.filter((item) => item.enabled && item.asset_type === 'etf').length,
  equity: items.value.filter((item) => item.enabled && item.asset_type === 'equity').length,
  watchlist: items.value.filter((item) => item.enabled && item.roles.equity_watchlist).length,
  options: items.value.filter((item) => item.enabled && item.collection.options).length,
  ai: items.value.filter((item) => item.enabled && item.roles.ai_candidate).length,
}))
const historyCollectionEnabled = computed(() => universe.value?.capacity?.enabled !== false)
const pendingStates = computed(() => Object.values(universe.value?.collection_states ?? {})
  .filter((state) => historyCollectionEnabled.value
    && ['pending_quota', 'admitted', 'collecting', 'retry_wait'].includes(state.access_state)))
const pendingCount = computed(() => pendingStates.value.filter((state) => state.access_state === 'pending_quota').length)
const capacityWarning = computed(() => {
  const capacity = universe.value?.capacity
  if (!capacity || capacity.quality_status === 'ok' || capacity.quality_status === 'disabled') return ''
  return capacity.warning || '无法读取 OpenD 历史 K 线额度；新增历史采集已安全暂停。'
})

function clone<T>(value: T): T { return JSON.parse(JSON.stringify(value)) as T }
function normalizeItem(item: InstrumentConfig): InstrumentConfig {
  const themes = Array.from(new Set(
    (item.themes?.length ? item.themes : [item.theme])
      .map((theme) => theme.trim())
      .filter(Boolean),
  ))
  return { ...item, themes, theme: themes[0] || item.theme.trim() }
}
function accept(payload: UniverseResponse) {
  universe.value = payload
  items.value = clone(payload.items).map(normalizeItem)
  fingerprint = JSON.stringify(items.value)
  selected.value = null
  themeDraft.value = ''
  confirming.value = false
  capacityPlan.value = null
}
async function load() {
  loading.value = true; error.value = ''
  try { accept(await api.getUniverse()) }
  catch (reason) { error.value = reason instanceof Error ? reason.message : '无法读取标的设置。' }
  finally { loading.value = false }
}
function addInstrument() {
  const item: InstrumentConfig = {
    symbol: '', display_name: '', asset_type: 'equity', theme: '个股观察', themes: ['个股观察'], enabled: true,
    roles: { market_benchmark: false, equity_watchlist: false, cta_proxy: false, options_collection: true, event_tracking: true, ai_candidate: true },
    benchmarks: { relative_strength: 'QQQ', cta_proxy_for: null },
    collection: { quote: true, daily_history: true, options: true }, notes: '',
  }
  items.value.push(item); selected.value = item
}
function selectInstrument(item: InstrumentConfig) {
  selected.value = item
  themeDraft.value = ''
}
function syncThemes(item: InstrumentConfig, themes: string[]) {
  const normalized = Array.from(new Set(themes.map((theme) => theme.trim()).filter(Boolean)))
  item.themes = normalized
  item.theme = normalized[0] || ''
}
function toggleTheme(item: InstrumentConfig, theme: string, checked: boolean) {
  const current = item.themes || [item.theme]
  if (!checked && current.length <= 1) {
    error.value = '至少保留一个关注列表；如不再关注，请停用或删除该标的。'
    return
  }
  syncThemes(item, checked ? [...current, theme] : current.filter((entry) => entry !== theme))
  error.value = ''
}
function addTheme() {
  const item = selected.value
  const theme = themeDraft.value.trim()
  if (!item || !theme) return
  if ((item.themes || []).includes(theme)) {
    themeDraft.value = ''
    return
  }
  syncThemes(item, [...(item.themes || []), theme])
  themeDraft.value = ''
}
function removeTheme(theme: string) {
  const item = selected.value
  if (!item) return
  if ((item.themes || []).length <= 1) {
    error.value = '至少保留一个主题；如不再适用，请删除或停用该标的。'
    return
  }
  syncThemes(item, (item.themes || []).filter((entry) => entry !== theme))
  error.value = ''
}
function toggleOptions(item: InstrumentConfig) { item.roles.options_collection = item.collection.options }
function toggleCta(item: InstrumentConfig) {
  if (item.roles.cta_proxy && !item.benchmarks.cta_proxy_for) item.benchmarks.cta_proxy_for = item.symbol || '待配置'
}
function requestRemoval(item: InstrumentConfig) {
  error.value = ''
  if (item.symbol === 'QQQ') {
    error.value = 'QQQ 是当前相对强弱算法的固定基准，不能删除；如需更换基准，需要先修改算法契约。'
    return
  }
  pendingRemoval.value = item
}
function removeInstrument() {
  const item = pendingRemoval.value
  if (!item) return
  const index = items.value.indexOf(item)
  if (index >= 0) items.value.splice(index, 1)
  if (selected.value === item) {
    selected.value = null
    themeDraft.value = ''
  }
  pendingRemoval.value = null
  notice.value = ''
}
function reset() { if (universe.value) accept(universe.value) }
function protectedItemsForSave() {
  const sourceBySymbol = new Map((universe.value?.items ?? []).map((item) => [item.symbol, item.roles.equity_watchlist]))
  return items.value.map((item) => ({
    ...item,
    roles: {
      ...item.roles,
      // Indicator recommendation membership is owned by the deployed
      // Universe. New local rows start outside it and existing values are
      // preserved even if a future editor accidentally exposes the field.
      equity_watchlist: sourceBySymbol.get(item.symbol) ?? false,
    },
  }))
}
function stateFor(symbol: string): HistoryCollectionState | undefined {
  return universe.value?.collection_states?.[symbol]
}
function stateLabel(state: HistoryCollectionState | undefined): string {
  if (!state) return ''
  if (state.access_state === 'pending_quota') return '等待日线额度'
  if (state.access_state === 'acquired' && state.quality_state === 'partial') return '日线已取得·样本偏短'
  if (state.access_state === 'acquired') return '日线已就绪'
  if (['admitted', 'collecting'].includes(state.access_state)) return '日线串行采集中'
  if (state.access_state === 'retry_wait') return '日线稍后重试'
  return state.access_state
}
function stateTone(state: HistoryCollectionState | undefined): string {
  if (!state) return ''
  if (state.access_state === 'pending_quota') return 'pending'
  if (state.access_state === 'acquired') return state.quality_state === 'partial' ? 'partial' : 'ready'
  return 'collecting'
}
async function refreshHistoryStatus() {
  if (!universe.value || polling.value) return
  polling.value = true
  try {
    const projection = await api.getUniverseHistoryStatus()
    if (universe.value) {
      universe.value = {
        ...universe.value,
        capacity: projection.capacity,
        collection_states: projection.states,
      }
    }
  } catch {
    // The settings page already shows the last authoritative projection; a
    // transient polling failure should not interrupt editing.
  } finally {
    polling.value = false
  }
}
async function beginSave() {
  if (!universe.value || saving.value || capacityLoading.value) return
  confirming.value = true
  capacityLoading.value = true
  capacityPlan.value = null
  error.value = ''
  try {
    capacityPlan.value = await api.createUniverseCapacityPlan({
      base_version_id: universe.value.version_id,
      items: protectedItemsForSave(),
    })
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '无法生成容量计划。'
  } finally {
    capacityLoading.value = false
  }
}
async function save() {
  if (!universe.value || saving.value || capacityLoading.value || !capacityPlan.value) return
  saving.value = true; error.value = ''; notice.value = ''
  try {
    accept(await api.updateUniverse({ base_version_id: universe.value.version_id, items: protectedItemsForSave() }))
    notice.value = '标的 Universe 已保存为新版本；后续任务会冻结并使用这个版本。'
    await refreshHistoryStatus()
  } catch (reason) {
    error.value = reason instanceof ApiError && reason.status === 409
      ? 'Universe 已被其他页面修改，请刷新后重试。'
      : reason instanceof Error ? reason.message : '保存失败。'
  } finally { saving.value = false }
}

onMounted(async () => {
  await load()
  pollTimer = setInterval(() => {
    if (pendingStates.value.length > 0) void refreshHistoryStatus()
  }, 30_000)
})
onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<template>
  <AppShell />
  <main class="page-shell universe-page">
    <header class="universe-header">
      <div>
        <p class="eyebrow">SETTINGS · INSTRUMENT UNIVERSE</p>
        <h1>标的设置</h1>
        <p>先按列表筛选，再点开个股快速调整主题归属、采集和策略角色。指标推荐由已部署 Universe 自动生成，只读；保存后只影响新任务，历史报告继续引用原版本。</p>
      </div>
      <RouterLink class="secondary-button" to="/settings">运行设置</RouterLink>
    </header>

    <div v-if="loading" class="state-panel">正在读取 Universe…</div>
    <div v-else-if="error && !universe" class="error-banner">{{ error }} <button @click="load">重试</button></div>
    <template v-else-if="universe">
      <section class="summary-strip">
        <span><strong>{{ summary.enabled }}</strong> 启用</span><span><strong>{{ summary.market }}</strong> 大盘</span>
        <span><strong>{{ summary.etf }}</strong> ETF</span><span><strong>{{ summary.equity }}</strong> 个股</span><span><strong>{{ summary.watchlist }}</strong> 指标推荐</span>
        <span><strong>{{ summary.options }}</strong> 期权</span><span><strong>{{ summary.ai }}</strong> AI 候选</span>
        <small>v{{ universe.revision }} · {{ universe.content_sha256.slice(0, 10) }}</small>
      </section>
      <section v-if="capacityWarning || pendingStates.length" class="capacity-banner" :data-warning="Boolean(capacityWarning)">
        <div>
          <strong>{{ capacityWarning ? 'OpenD 历史容量需要注意' : '历史日线正在慢速采集' }}</strong>
          <p v-if="capacityWarning">{{ capacityWarning }}</p>
          <p v-else>{{ pendingCount }} 个标的等待额度释放；后续每日串行采集会自动重试，配置不会被自动停用。</p>
        </div>
        <button type="button" class="secondary-button" :disabled="polling" @click="refreshHistoryStatus">{{ polling ? '刷新中…' : '刷新状态' }}</button>
      </section>

      <section class="watchlist-quickbar" aria-labelledby="watchlist-quickbar-title">
        <div class="watchlist-quickbar-heading">
          <div>
            <p class="eyebrow">QUICK WATCHLIST EDITOR</p>
            <h2 id="watchlist-quickbar-title">关注列表</h2>
          </div>
          <p>“指标推荐”来自已部署 Universe 的自动同步，只能查看和筛选；其余列表是可编辑的主题归属。同一只个股可以同时属于多个主题列表。</p>
        </div>
        <div class="watchlist-tabs" role="tablist" aria-label="按关注列表筛选">
          <button type="button" :class="{ active: listFilter === 'all' }" @click="listFilter = 'all'">全部 <small>{{ items.length }}</small></button>
          <button type="button" :class="{ active: listFilter === 'core' }" @click="listFilter = 'core'">指标推荐 <small>{{ summary.watchlist }}</small></button>
          <button v-for="list in watchlistOptions" :key="list.name" type="button" :class="{ active: listFilter === list.name }" @click="listFilter = list.name">{{ list.name }} <small>{{ list.count }}</small></button>
        </div>
      </section>

      <section class="universe-toolbar">
        <input v-model="query" type="search" placeholder="搜索 Symbol、名称或主题" />
        <div class="filter-tabs">
          <button v-for="entry in [['all','全部'],['market','大盘'],['etf','ETF'],['equity','个股'],['options','期权'],['cta','CTA'],['ai','AI 候选']]" :key="entry[0]" :class="{ active: filter === entry[0] }" @click="filter = entry[0] as typeof filter">{{ entry[1] }}</button>
        </div>
        <button class="primary-button" @click="addInstrument">添加标的</button>
      </section>

      <section class="universe-table-wrap">
        <table class="universe-table">
          <thead><tr><th>状态</th><th>标的</th><th>类型</th><th>所属关注列表</th><th>采集</th><th>策略角色</th><th>相对基准</th><th aria-label="操作"></th></tr></thead>
          <tbody>
            <tr v-for="item in filtered" :key="item.symbol || items.indexOf(item)" :data-disabled="!item.enabled" @click="selectInstrument(item)">
              <td><span class="status-dot" :data-enabled="item.enabled"></span>{{ item.enabled ? '启用' : '停用' }}<span v-if="stateFor(item.symbol)" class="collection-badge" :data-tone="stateTone(stateFor(item.symbol))" :title="stateFor(item.symbol)?.message || undefined">{{ stateLabel(stateFor(item.symbol)) }}</span></td>
              <td><strong>{{ item.symbol || '未命名' }}</strong><small>{{ item.display_name || '待填写名称' }}</small></td>
              <td><span class="type-tag">{{ item.asset_type === 'market' ? '大盘' : item.asset_type === 'etf' ? 'ETF' : '个股' }}</span></td>
              <td><div class="table-theme-list"><span v-if="item.roles.equity_watchlist" class="table-theme-chip table-core-chip">指标推荐</span><span v-for="theme in (item.themes || [item.theme])" :key="theme" class="table-theme-chip">{{ theme }}</span></div></td>
              <td><span v-if="item.collection.quote">报价</span> · <span v-if="item.collection.daily_history">日线</span><span v-if="item.collection.options"> · 期权</span></td>
              <td><span v-if="item.roles.cta_proxy">CTA </span><span v-if="item.roles.ai_candidate">AI </span><span v-if="item.roles.event_tracking">事件</span></td>
              <td>{{ item.benchmarks.relative_strength || '—' }}</td>
              <td class="row-actions"><button type="button" aria-label="删除标的" @click.stop="requestRemoval(item)">删除</button></td>
            </tr>
          </tbody>
        </table>
      </section>

      <div v-if="error" class="error-banner">{{ error }}</div><div v-if="notice" class="success-banner">{{ notice }}</div>
      <footer class="universe-actions">
        <span>{{ dirty ? '有未保存修改' : '已与当前版本同步' }}</span>
        <div><button class="secondary-button" :disabled="!dirty || saving || capacityLoading" @click="reset">撤销</button><button class="primary-button" :disabled="!dirty || saving || capacityLoading" @click="beginSave">{{ capacityLoading ? '计算容量…' : '保存新版本' }}</button></div>
      </footer>

      <aside v-if="selected" class="instrument-drawer" aria-label="标的编辑面板">
        <header><div><p class="eyebrow">INSTRUMENT CONFIG</p><h2>{{ selected.symbol || '新增标的' }}</h2></div><button aria-label="关闭" @click="selected = null">×</button></header>
        <label>Symbol<input v-model.trim="selected.symbol" maxlength="16" @input="selected.symbol = selected.symbol.toUpperCase()" /></label>
        <label>显示名称<input v-model.trim="selected.display_name" /></label>
        <label>类型<select v-model="selected.asset_type"><option value="market">大盘</option><option value="etf">ETF</option><option value="equity">个股</option></select></label>
        <div class="theme-editor">
          <span class="field-label">所属关注列表</span>
          <div class="list-check-grid">
            <label v-for="list in watchlistOptions" :key="list.name" class="list-check">
              <input
                type="checkbox"
                :checked="(selected.themes || [selected.theme]).includes(list.name)"
                @change="toggleTheme(selected, list.name, ($event.target as HTMLInputElement).checked)"
              />
              <span>{{ list.name }}</span>
              <small>{{ list.count }}</small>
            </label>
          </div>
          <div class="theme-chip-list">
            <span v-for="theme in (selected.themes || [])" :key="theme" class="theme-chip">
              {{ theme }}
              <button type="button" :aria-label="`移除主题 ${theme}`" @click="removeTheme(theme)">×</button>
            </span>
          </div>
          <div class="theme-add-row">
            <input v-model.trim="themeDraft" placeholder="输入主题，回车或点击添加" @keydown.enter.prevent="addTheme" />
            <button type="button" class="secondary-button" @click="addTheme">添加</button>
          </div>
          <small>勾选即可调整归属；下方可新建列表。首个列表作为主标签。</small>
        </div>
        <label class="check"><input v-model="selected.enabled" type="checkbox" /> 启用该标的</label>
        <fieldset><legend>数据采集</legend><label class="check"><input v-model="selected.collection.quote" type="checkbox" /> 报价</label><label class="check"><input v-model="selected.collection.daily_history" type="checkbox" /> 日线 / 技术指标</label><label class="check"><input v-model="selected.collection.options" type="checkbox" @change="toggleOptions(selected)" /> 期权结构</label></fieldset>
        <fieldset><legend>策略角色</legend><div class="protected-role"><strong>指标推荐</strong><small>{{ selected.roles.equity_watchlist ? '已由部署 Universe 纳入' : '当前未纳入，由部署 Universe 决定' }}</small></div><label class="check"><input v-model="selected.roles.market_benchmark" type="checkbox" /> 市场基准</label><label class="check"><input v-model="selected.roles.ai_candidate" type="checkbox" /> AI 候选</label><label class="check"><input v-model="selected.roles.cta_proxy" type="checkbox" @change="toggleCta(selected)" /> CTA 代理</label><label class="check"><input v-model="selected.roles.event_tracking" type="checkbox" /> 事件跟踪</label></fieldset>
        <label>相对强弱基准<input v-model.trim="selected.benchmarks.relative_strength" placeholder="QQQ" /></label>
        <label v-if="selected.roles.cta_proxy">CTA 代表对象<input v-model.trim="selected.benchmarks.cta_proxy_for" placeholder="例如 NQ / GC" /></label>
        <label>备注<textarea v-model.trim="selected.notes" rows="3"></textarea></label>
      </aside>

      <div v-if="confirming" class="confirm-backdrop" @click.self="confirming = false"><section class="confirm-card"><p class="eyebrow">CREATE IMMUTABLE VERSION</p><h2>保存 Universe 新版本？</h2><p>新任务将使用 {{ summary.enabled }} 个启用标的，其中 {{ summary.ai }} 个进入 AI 分析、{{ summary.options }} 个进入每日期权队列。正在运行和历史任务不会改变。</p><div v-if="capacityLoading" class="capacity-plan-loading">正在读取 OpenD 额度并检查本地日线缓存…</div><template v-else-if="capacityPlan"><div class="capacity-plan-summary"><div><strong>{{ capacityPlan.summary.admitted_new_slot_count ?? 0 }}</strong><small>新增日线槽位已准入</small></div><div><strong>{{ capacityPlan.summary.pending_quota_count ?? 0 }}</strong><small>等待额度</small></div><div><strong>{{ capacityPlan.summary.cache_ready_count ?? 0 }}</strong><small>本地缓存命中</small></div></div><ul v-if="capacityPlan.symbols.some((item) => item.decision === 'pending_quota')" class="capacity-plan-pending"><li v-for="item in capacityPlan.symbols.filter((entry) => entry.decision === 'pending_quota')" :key="item.symbol"><strong>{{ item.symbol }}</strong> 等待历史 K 线额度释放</li></ul><p v-if="capacityPlan.warnings.length" class="capacity-plan-warning">{{ capacityPlan.warnings.join('；') }}</p></template><div><button class="secondary-button" @click="confirming = false">取消</button><button class="primary-button" :disabled="saving || capacityLoading || !capacityPlan" @click="save">{{ saving ? '保存中…' : '确认保存' }}</button></div></section></div>
      <div v-if="pendingRemoval" class="confirm-backdrop" @click.self="pendingRemoval = null"><section class="confirm-card"><p class="eyebrow">REMOVE FROM NEXT VERSION</p><h2>删除 {{ pendingRemoval.symbol || '这个新增标的' }}？</h2><p>它只会从待保存的新 Universe 版本移除。历史版本、已完成报告和正在运行的任务仍保留原配置。</p><div><button class="secondary-button" @click="pendingRemoval = null">取消</button><button class="danger-button" @click="removeInstrument">确认删除</button></div></section></div>
    </template>
  </main>
</template>

<style scoped>
.universe-page{max-width:1180px;padding-bottom:90px}.universe-header{display:flex;justify-content:space-between;align-items:end;gap:24px;padding:38px 0 25px;border-top:1px solid var(--line)}.universe-header h1{margin:8px 0;font-size:48px}.universe-header p:last-child{color:var(--soft-ink);font-size:13px}.summary-strip{display:flex;align-items:center;gap:0;border:1px solid var(--line);border-radius:8px;background:var(--surface)}.summary-strip span{padding:13px 16px;border-right:1px solid var(--line);color:var(--muted);font:10px monospace}.summary-strip strong{color:var(--ink);font-size:16px}.summary-strip small{margin-left:auto;padding:0 14px;color:var(--muted);font:9px monospace}.watchlist-quickbar{margin-top:16px;padding:17px 18px;border:1px solid var(--line);border-radius:8px;background:linear-gradient(120deg,rgba(182,79,56,.1),rgba(255,255,255,.01))}.watchlist-quickbar-heading{display:flex;align-items:end;justify-content:space-between;gap:20px}.watchlist-quickbar-heading h2{margin-top:7px;font-size:22px}.watchlist-quickbar-heading>p{max-width:430px;color:var(--muted);font:10px/1.5 monospace;text-align:right}.watchlist-tabs{display:flex;gap:7px;flex-wrap:wrap;margin-top:14px}.watchlist-tabs button{padding:8px 11px;border:1px solid var(--line);border-radius:999px;background:transparent;color:var(--soft-ink);font-size:11px;cursor:pointer}.watchlist-tabs button:hover,.watchlist-tabs button.active{border-color:var(--accent);background:rgba(182,79,56,.14);color:var(--ink)}.watchlist-tabs small{margin-left:4px;color:var(--muted);font:9px monospace}.universe-toolbar{display:flex;gap:10px;align-items:center;margin:18px 0 10px}.universe-toolbar>input{width:230px;min-height:38px;padding:0 11px;border:1px solid var(--line);border-radius:6px;background:var(--surface);color:var(--ink)}.filter-tabs{display:flex;flex:1}.filter-tabs button{padding:9px 12px;border:1px solid var(--line);border-right:0;background:transparent;color:var(--muted)}.filter-tabs button:first-child{border-radius:6px 0 0 6px}.filter-tabs button:last-child{border-right:1px solid var(--line);border-radius:0 6px 6px 0}.filter-tabs button.active{background:var(--surface-raised);color:var(--accent)}.universe-table-wrap{overflow:auto;border:1px solid var(--line);border-radius:8px}.universe-table{width:100%;border-collapse:collapse;font-size:12px}.universe-table th{text-align:left;padding:10px 12px;color:var(--muted);font:9px monospace;border-bottom:1px solid var(--line)}.universe-table td{padding:12px;border-bottom:1px solid var(--line-soft);cursor:pointer}.universe-table tr:hover td{background:rgba(182,79,56,.06)}.universe-table tr[data-disabled=true]{opacity:.48}.universe-table td small{display:block;margin-top:4px;color:var(--muted)}.status-dot{display:inline-block;width:6px;height:6px;margin-right:6px;border-radius:50%;background:var(--muted)}.status-dot[data-enabled=true]{background:var(--success)}.type-tag{color:var(--accent)}.table-theme-list{display:flex;flex-wrap:wrap;gap:4px;min-width:150px}.table-theme-chip{display:inline-block;padding:3px 6px;border:1px solid var(--line);border-radius:999px;color:var(--soft-ink);font:9px monospace;white-space:nowrap}.universe-actions{display:flex;justify-content:space-between;align-items:center;margin-top:15px;padding-top:15px;border-top:1px solid var(--line);color:var(--muted);font:10px monospace}.universe-actions div,.confirm-card div{display:flex;gap:8px}.error-banner,.success-banner,.state-panel{margin-top:14px;padding:12px;border:1px solid var(--line);border-radius:7px}.success-banner{color:var(--success)}.instrument-drawer{position:fixed;z-index:30;top:0;right:0;width:min(430px,100vw);height:100vh;overflow:auto;padding:28px;border-left:1px solid var(--line);background:var(--surface);box-shadow:-16px 0 45px rgba(0,0,0,.35)}.instrument-drawer header{display:flex;justify-content:space-between;margin-bottom:24px}.instrument-drawer header button{border:0;background:none;color:var(--ink);font-size:28px}.instrument-drawer h2{margin-top:7px}.instrument-drawer>label,.field-grid label{display:grid;gap:6px;margin:13px 0;color:var(--muted);font:10px monospace}.instrument-drawer input,.instrument-drawer select,.instrument-drawer textarea{width:100%;padding:10px;border:1px solid var(--line);border-radius:5px;background:var(--surface-raised);color:var(--ink)}.field-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.instrument-drawer fieldset{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin:17px 0;padding:13px;border:1px solid var(--line);border-radius:7px}.instrument-drawer legend{padding:0 5px;color:var(--muted);font:9px monospace}.instrument-drawer .check{display:flex;align-items:center;gap:7px;margin:0;color:var(--soft-ink)}.instrument-drawer .check input{width:auto}.list-check-grid{display:grid;grid-template-columns:1fr 1fr;gap:7px;padding:10px;border:1px solid var(--line-soft);border-radius:6px;background:var(--surface-raised)}.list-check{display:flex;align-items:center;gap:7px;padding:6px;color:var(--soft-ink);font-size:11px;cursor:pointer}.list-check input{width:auto!important;padding:0!important}.list-check small{margin-left:auto;color:var(--muted);font:9px monospace}.confirm-backdrop{position:fixed;z-index:50;inset:0;display:grid;place-items:center;background:rgba(8,6,5,.72)}.confirm-card{width:min(520px,calc(100vw - 30px));padding:25px;border:1px solid var(--line);border-radius:10px;background:var(--surface)}.confirm-card h2{margin:8px 0 12px}.confirm-card p{color:var(--soft-ink);line-height:1.6}.confirm-card div{justify-content:flex-end;margin-top:20px}@media(max-width:800px){.universe-header,.universe-toolbar,.universe-actions{align-items:stretch;flex-direction:column}.summary-strip{overflow:auto}.filter-tabs,.watchlist-tabs{overflow:auto}.watchlist-quickbar-heading{align-items:stretch;flex-direction:column}.watchlist-quickbar-heading>p{text-align:left}.universe-toolbar>input{width:100%}}
.row-actions{text-align:right}.row-actions button{padding:5px 8px;border:1px solid transparent;border-radius:4px;background:transparent;color:var(--muted);font:10px monospace}.row-actions button:hover{border-color:var(--danger);color:var(--danger)}.danger-button{min-height:38px;padding:0 14px;border:1px solid var(--danger);border-radius:5px;background:rgba(176,65,55,.14);color:var(--danger);cursor:pointer}.theme-editor{display:grid;gap:8px;margin:13px 0;color:var(--muted);font:10px monospace}.theme-editor .field-label{color:var(--muted)}.theme-chip-list{display:flex;flex-wrap:wrap;gap:6px;min-height:28px}.theme-chip{display:inline-flex;align-items:center;gap:5px;padding:5px 8px;border:1px solid var(--accent);border-radius:999px;background:rgba(182,79,56,.1);color:var(--ink);font-size:11px}.theme-chip button{padding:0;border:0;background:transparent;color:var(--muted);font-size:15px;line-height:1;cursor:pointer}.theme-chip button:hover{color:var(--danger)}.theme-add-row{display:flex;gap:7px}.theme-add-row input{flex:1}.theme-add-row .secondary-button{min-height:36px;padding:0 12px}.theme-editor>small{color:var(--muted);line-height:1.5}.theme-list{max-width:250px;white-space:normal}
.table-core-chip{border-color:var(--accent);background:rgba(182,79,56,.14);color:var(--ink)}
.protected-role{display:grid;gap:4px;min-height:32px;padding:7px 8px;border:1px solid var(--accent);border-radius:5px;background:rgba(182,79,56,.08);color:var(--ink);font-size:11px}.protected-role small{color:var(--muted);font:9px/1.35 monospace}
.capacity-banner{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-top:12px;padding:13px 16px;border:1px solid rgba(184,139,49,.55);border-radius:8px;background:rgba(184,139,49,.1);color:var(--ink)}.capacity-banner[data-warning="true"]{border-color:rgba(176,65,55,.65);background:rgba(176,65,55,.1)}.capacity-banner strong{font-size:12px}.capacity-banner p{margin:5px 0 0;color:var(--muted);font:10px/1.5 monospace}.collection-badge{display:inline-block;margin-top:4px;padding:3px 6px;border:1px solid var(--line);border-radius:999px;color:var(--muted);font:9px/1 monospace}.collection-badge[data-tone="pending"]{border-color:#b88b31;color:#a4761e;background:rgba(184,139,49,.12)}.collection-badge[data-tone="collecting"]{border-color:#5c7fa8;color:#486887;background:rgba(92,127,168,.12)}.collection-badge[data-tone="ready"]{border-color:#5b936f;color:#4b7b5d;background:rgba(91,147,111,.1)}.collection-badge[data-tone="partial"]{border-color:#b88b31;color:#a4761e;background:rgba(184,139,49,.1)}.capacity-plan-loading{margin:16px 0;padding:13px;border:1px dashed var(--line);color:var(--muted);font:11px monospace}.capacity-plan-summary{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:16px 0}.capacity-plan-summary div{display:grid;gap:4px;padding:10px;border:1px solid var(--line);border-radius:6px;background:var(--surface-raised);text-align:center}.capacity-plan-summary strong{font-size:20px}.capacity-plan-summary small{color:var(--muted);font:9px monospace}.capacity-plan-pending{max-height:130px;overflow:auto;margin:0;padding:9px 10px 9px 25px;border:1px solid rgba(184,139,49,.45);border-radius:6px;background:rgba(184,139,49,.08);color:var(--soft-ink);font:10px/1.6 monospace}.capacity-plan-warning{padding:9px 10px;border-left:2px solid var(--danger);color:var(--danger)!important;font:10px/1.5 monospace!important}@media(max-width:800px){.capacity-banner{align-items:stretch;flex-direction:column}.capacity-plan-summary{grid-template-columns:1fr 1fr}.capacity-plan-summary div:last-child{grid-column:span 2}}
</style>
