<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { ApiError, api } from '@/api/client'
import AppShell from '@/components/AppShell.vue'
import type { AssetType, InstrumentConfig, UniverseResponse } from '@/types/universe'

const universe = ref<UniverseResponse | null>(null)
const items = ref<InstrumentConfig[]>([])
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const query = ref('')
const filter = ref<'all' | AssetType | 'options' | 'cta' | 'ai'>('all')
const selected = ref<InstrumentConfig | null>(null)
const confirming = ref(false)
const pendingRemoval = ref<InstrumentConfig | null>(null)
let fingerprint = ''

const dirty = computed(() => JSON.stringify(items.value) !== fingerprint)
const filtered = computed(() => items.value.filter((item) => {
  const text = query.value.trim().toUpperCase()
  const matchesText = !text || `${item.symbol} ${item.display_name} ${item.theme}`.toUpperCase().includes(text)
  const matchesFilter = filter.value === 'all'
    || item.asset_type === filter.value
    || (filter.value === 'options' && item.collection.options)
    || (filter.value === 'cta' && item.roles.cta_proxy)
    || (filter.value === 'ai' && item.roles.ai_candidate)
  return matchesText && matchesFilter
}))
const summary = computed(() => ({
  enabled: items.value.filter((item) => item.enabled).length,
  market: items.value.filter((item) => item.enabled && item.asset_type === 'market').length,
  etf: items.value.filter((item) => item.enabled && item.asset_type === 'etf').length,
  equity: items.value.filter((item) => item.enabled && item.asset_type === 'equity').length,
  options: items.value.filter((item) => item.enabled && item.collection.options).length,
  ai: items.value.filter((item) => item.enabled && item.roles.ai_candidate).length,
}))

function clone<T>(value: T): T { return JSON.parse(JSON.stringify(value)) as T }
function accept(payload: UniverseResponse) {
  universe.value = payload
  items.value = clone(payload.items)
  fingerprint = JSON.stringify(items.value)
  selected.value = null
  confirming.value = false
}
async function load() {
  loading.value = true; error.value = ''
  try { accept(await api.getUniverse()) }
  catch (reason) { error.value = reason instanceof Error ? reason.message : '无法读取标的设置。' }
  finally { loading.value = false }
}
function addInstrument() {
  const item: InstrumentConfig = {
    symbol: '', display_name: '', asset_type: 'equity', theme: '个股观察', enabled: true,
    roles: { market_benchmark: false, equity_watchlist: true, cta_proxy: false, options_collection: false, event_tracking: true, ai_candidate: true },
    benchmarks: { relative_strength: 'QQQ', cta_proxy_for: null },
    collection: { quote: true, daily_history: true, options: false }, notes: '',
  }
  items.value.push(item); selected.value = item
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
  if (selected.value === item) selected.value = null
  pendingRemoval.value = null
  notice.value = ''
}
function reset() { if (universe.value) accept(universe.value) }
async function save() {
  if (!universe.value || saving.value) return
  saving.value = true; error.value = ''; notice.value = ''
  try {
    accept(await api.updateUniverse({ base_version_id: universe.value.version_id, items: items.value }))
    notice.value = '标的 Universe 已保存为新版本；后续任务会冻结并使用这个版本。'
  } catch (reason) {
    error.value = reason instanceof ApiError && reason.status === 409
      ? 'Universe 已被其他页面修改，请刷新后重试。'
      : reason instanceof Error ? reason.message : '保存失败。'
  } finally { saving.value = false }
}

onMounted(load)
</script>

<template>
  <AppShell />
  <main class="page-shell universe-page">
    <header class="universe-header">
      <div>
        <p class="eyebrow">SETTINGS · INSTRUMENT UNIVERSE</p>
        <h1>标的设置</h1>
        <p>统一管理大盘代理、ETF 与个股。保存后只影响新任务，历史报告继续引用原版本。</p>
      </div>
      <RouterLink class="secondary-button" to="/settings">运行设置</RouterLink>
    </header>

    <div v-if="loading" class="state-panel">正在读取 Universe…</div>
    <div v-else-if="error && !universe" class="error-banner">{{ error }} <button @click="load">重试</button></div>
    <template v-else-if="universe">
      <section class="summary-strip">
        <span><strong>{{ summary.enabled }}</strong> 启用</span><span><strong>{{ summary.market }}</strong> 大盘</span>
        <span><strong>{{ summary.etf }}</strong> ETF</span><span><strong>{{ summary.equity }}</strong> 个股</span>
        <span><strong>{{ summary.options }}</strong> 期权</span><span><strong>{{ summary.ai }}</strong> AI 候选</span>
        <small>v{{ universe.revision }} · {{ universe.content_sha256.slice(0, 10) }}</small>
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
          <thead><tr><th>状态</th><th>标的</th><th>类型 / 主题</th><th>采集</th><th>策略角色</th><th>相对基准</th><th aria-label="操作"></th></tr></thead>
          <tbody>
            <tr v-for="item in filtered" :key="item.symbol || items.indexOf(item)" :data-disabled="!item.enabled" @click="selected = item">
              <td><span class="status-dot" :data-enabled="item.enabled"></span>{{ item.enabled ? '启用' : '停用' }}</td>
              <td><strong>{{ item.symbol || '未命名' }}</strong><small>{{ item.display_name || '待填写名称' }}</small></td>
              <td><span class="type-tag">{{ item.asset_type === 'market' ? '大盘' : item.asset_type === 'etf' ? 'ETF' : '个股' }}</span><small>{{ item.theme }}</small></td>
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
        <div><button class="secondary-button" :disabled="!dirty || saving" @click="reset">撤销</button><button class="primary-button" :disabled="!dirty || saving" @click="confirming = true">保存新版本</button></div>
      </footer>

      <aside v-if="selected" class="instrument-drawer" aria-label="标的编辑面板">
        <header><div><p class="eyebrow">INSTRUMENT CONFIG</p><h2>{{ selected.symbol || '新增标的' }}</h2></div><button aria-label="关闭" @click="selected = null">×</button></header>
        <label>Symbol<input v-model.trim="selected.symbol" maxlength="16" @input="selected.symbol = selected.symbol.toUpperCase()" /></label>
        <label>显示名称<input v-model.trim="selected.display_name" /></label>
        <div class="field-grid"><label>类型<select v-model="selected.asset_type"><option value="market">大盘</option><option value="etf">ETF</option><option value="equity">个股</option></select></label><label>主题<input v-model.trim="selected.theme" /></label></div>
        <label class="check"><input v-model="selected.enabled" type="checkbox" /> 启用该标的</label>
        <fieldset><legend>数据采集</legend><label class="check"><input v-model="selected.collection.quote" type="checkbox" /> 报价</label><label class="check"><input v-model="selected.collection.daily_history" type="checkbox" /> 日线 / 技术指标</label><label class="check"><input v-model="selected.collection.options" type="checkbox" @change="toggleOptions(selected)" /> 期权结构</label></fieldset>
        <fieldset><legend>策略角色</legend><label class="check"><input v-model="selected.roles.market_benchmark" type="checkbox" /> 市场基准</label><label class="check"><input v-model="selected.roles.ai_candidate" type="checkbox" /> AI 候选</label><label class="check"><input v-model="selected.roles.cta_proxy" type="checkbox" @change="toggleCta(selected)" /> CTA 代理</label><label class="check"><input v-model="selected.roles.event_tracking" type="checkbox" /> 事件跟踪</label></fieldset>
        <label>相对强弱基准<input v-model.trim="selected.benchmarks.relative_strength" placeholder="QQQ" /></label>
        <label v-if="selected.roles.cta_proxy">CTA 代表对象<input v-model.trim="selected.benchmarks.cta_proxy_for" placeholder="例如 NQ / GC" /></label>
        <label>备注<textarea v-model.trim="selected.notes" rows="3"></textarea></label>
      </aside>

      <div v-if="confirming" class="confirm-backdrop" @click.self="confirming = false"><section class="confirm-card"><p class="eyebrow">CREATE IMMUTABLE VERSION</p><h2>保存 Universe 新版本？</h2><p>新任务将使用 {{ summary.enabled }} 个启用标的，其中 {{ summary.ai }} 个进入 AI 分析、{{ summary.options }} 个采集期权。正在运行和历史任务不会改变。</p><div><button class="secondary-button" @click="confirming = false">取消</button><button class="primary-button" :disabled="saving" @click="save">{{ saving ? '保存中…' : '确认保存' }}</button></div></section></div>
      <div v-if="pendingRemoval" class="confirm-backdrop" @click.self="pendingRemoval = null"><section class="confirm-card"><p class="eyebrow">REMOVE FROM NEXT VERSION</p><h2>删除 {{ pendingRemoval.symbol || '这个新增标的' }}？</h2><p>它只会从待保存的新 Universe 版本移除。历史版本、已完成报告和正在运行的任务仍保留原配置。</p><div><button class="secondary-button" @click="pendingRemoval = null">取消</button><button class="danger-button" @click="removeInstrument">确认删除</button></div></section></div>
    </template>
  </main>
</template>

<style scoped>
.universe-page{max-width:1180px;padding-bottom:90px}.universe-header{display:flex;justify-content:space-between;align-items:end;gap:24px;padding:38px 0 25px;border-top:1px solid var(--line)}.universe-header h1{margin:8px 0;font-size:48px}.universe-header p:last-child{color:var(--soft-ink);font-size:13px}.summary-strip{display:flex;align-items:center;gap:0;border:1px solid var(--line);border-radius:8px;background:var(--surface)}.summary-strip span{padding:13px 16px;border-right:1px solid var(--line);color:var(--muted);font:10px monospace}.summary-strip strong{color:var(--ink);font-size:16px}.summary-strip small{margin-left:auto;padding:0 14px;color:var(--muted);font:9px monospace}.universe-toolbar{display:flex;gap:10px;align-items:center;margin:18px 0 10px}.universe-toolbar>input{width:230px;min-height:38px;padding:0 11px;border:1px solid var(--line);border-radius:6px;background:var(--surface);color:var(--ink)}.filter-tabs{display:flex;flex:1}.filter-tabs button{padding:9px 12px;border:1px solid var(--line);border-right:0;background:transparent;color:var(--muted)}.filter-tabs button:first-child{border-radius:6px 0 0 6px}.filter-tabs button:last-child{border-right:1px solid var(--line);border-radius:0 6px 6px 0}.filter-tabs button.active{background:var(--surface-raised);color:var(--accent)}.universe-table-wrap{overflow:auto;border:1px solid var(--line);border-radius:8px}.universe-table{width:100%;border-collapse:collapse;font-size:12px}.universe-table th{text-align:left;padding:10px 12px;color:var(--muted);font:9px monospace;border-bottom:1px solid var(--line)}.universe-table td{padding:12px;border-bottom:1px solid var(--line-soft);cursor:pointer}.universe-table tr:hover td{background:rgba(182,79,56,.06)}.universe-table tr[data-disabled=true]{opacity:.48}.universe-table td small{display:block;margin-top:4px;color:var(--muted)}.status-dot{display:inline-block;width:6px;height:6px;margin-right:6px;border-radius:50%;background:var(--muted)}.status-dot[data-enabled=true]{background:var(--success)}.type-tag{color:var(--accent)}.universe-actions{display:flex;justify-content:space-between;align-items:center;margin-top:15px;padding-top:15px;border-top:1px solid var(--line);color:var(--muted);font:10px monospace}.universe-actions div,.confirm-card div{display:flex;gap:8px}.error-banner,.success-banner,.state-panel{margin-top:14px;padding:12px;border:1px solid var(--line);border-radius:7px}.success-banner{color:var(--success)}.instrument-drawer{position:fixed;z-index:30;top:0;right:0;width:min(430px,100vw);height:100vh;overflow:auto;padding:28px;border-left:1px solid var(--line);background:var(--surface);box-shadow:-16px 0 45px rgba(0,0,0,.35)}.instrument-drawer header{display:flex;justify-content:space-between;margin-bottom:24px}.instrument-drawer header button{border:0;background:none;color:var(--ink);font-size:28px}.instrument-drawer h2{margin-top:7px}.instrument-drawer>label,.field-grid label{display:grid;gap:6px;margin:13px 0;color:var(--muted);font:10px monospace}.instrument-drawer input,.instrument-drawer select,.instrument-drawer textarea{width:100%;padding:10px;border:1px solid var(--line);border-radius:5px;background:var(--surface-raised);color:var(--ink)}.field-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.instrument-drawer fieldset{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin:17px 0;padding:13px;border:1px solid var(--line);border-radius:7px}.instrument-drawer legend{padding:0 5px;color:var(--muted);font:9px monospace}.instrument-drawer .check{display:flex;align-items:center;gap:7px;margin:0;color:var(--soft-ink)}.instrument-drawer .check input{width:auto}.confirm-backdrop{position:fixed;z-index:50;inset:0;display:grid;place-items:center;background:rgba(8,6,5,.72)}.confirm-card{width:min(520px,calc(100vw - 30px));padding:25px;border:1px solid var(--line);border-radius:10px;background:var(--surface)}.confirm-card h2{margin:8px 0 12px}.confirm-card p{color:var(--soft-ink);line-height:1.6}.confirm-card div{justify-content:flex-end;margin-top:20px}@media(max-width:800px){.universe-header,.universe-toolbar,.universe-actions{align-items:stretch;flex-direction:column}.summary-strip{overflow:auto}.filter-tabs{overflow:auto}.universe-toolbar>input{width:100%}}
.row-actions{text-align:right}.row-actions button{padding:5px 8px;border:1px solid transparent;border-radius:4px;background:transparent;color:var(--muted);font:10px monospace}.row-actions button:hover{border-color:var(--danger);color:var(--danger)}.danger-button{min-height:38px;padding:0 14px;border:1px solid var(--danger);border-radius:5px;background:rgba(176,65,55,.14);color:var(--danger);cursor:pointer}
</style>
