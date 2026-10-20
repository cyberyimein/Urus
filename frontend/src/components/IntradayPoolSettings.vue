<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { ApiError } from '@/api/client'
import { intradayPoolApi } from '@/api/intradayPool'
import type { IntradayPoolMember, IntradayPoolSettings } from '@/types/intradayPool'

const settings = ref<IntradayPoolSettings | null>(null)
const selected = ref<string[]>([])
const query = ref('')
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const conflict = ref(false)
const section = ref<HTMLElement | null>(null)
let savedSelection = ''

function selectionKey(symbols: string[]) {
  return [...symbols].sort().join(',')
}
const dirty = computed(() => settings.value?.status === 'unconfigured' || selectionKey(selected.value) !== savedSelection)
const total = computed(() => new Set([...selected.value, ...(settings.value?.benchmark_symbols ?? [])]).size)
const groups = computed(() => {
  const result = new Map<string, IntradayPoolMember[]>()
  const text = query.value.trim().toUpperCase()
  for (const member of settings.value?.available_symbols ?? []) {
    if (text && !`${member.symbol} ${member.display_name} ${member.display_group}`.toUpperCase().includes(text)) continue
    const name = member.display_group || '未分组'
    if (!result.has(name)) result.set(name, [])
    result.get(name)!.push(member)
  }
  return Array.from(result, ([name, members]) => ({ name, members }))
})
function isFixed(member: IntradayPoolMember) {
  return member.fixed || Boolean(settings.value?.benchmark_symbols.includes(member.symbol))
}
function applySettings(value: IntradayPoolSettings) {
  settings.value = value
  selected.value = [...(value.pending ?? value.active)?.selected_symbols ?? []]
  savedSelection = selectionKey(selected.value)
}
function reset() {
  if (settings.value) applySettings(settings.value)
  notice.value = ''
}
async function load() {
  loading.value = true
  error.value = ''
  try {
    applySettings(await intradayPoolApi.get())
    conflict.value = false
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '日内标的池读取失败'
  } finally {
    loading.value = false
  }
}
async function save() {
  if (!settings.value || saving.value || conflict.value) return
  saving.value = true
  error.value = ''
  notice.value = ''
  try {
    const benchmarks = settings.value.benchmark_symbols
    applySettings(await intradayPoolApi.save(settings.value.revision, selected.value.filter(symbol => !benchmarks.includes(symbol))))
    notice.value = `已保存，将于 ${settings.value?.pending?.effective_date ?? '下一交易日'} 生效；当前交易日的采集配置保持不变。`
  } catch (cause) {
    conflict.value = cause instanceof ApiError && cause.status === 409
    error.value = conflict.value ? '配置已发生变化，请重新读取后再修改。' : cause instanceof Error ? cause.message : '保存失败，请重试'
  } finally {
    saving.value = false
  }
}
onMounted(async () => {
  await load()
  await nextTick()
  if (window.location.hash === '#intraday-pool') section.value?.scrollIntoView({ block: 'start' })
})
</script>

<template>
  <section id="intraday-pool" ref="section" class="pool-settings" aria-labelledby="pool-settings-title">
    <header>
      <div><p class="eyebrow">INTRADAY OBSERVATION</p><h2 id="pool-settings-title">日内观察标的池</h2></div>
      <RouterLink to="/intraday" class="secondary-button">返回日内观察</RouterLink>
    </header>
    <p class="pool-help">修改后从下一个美股交易日开始采集。SPY、QQQ、IWM 为固定大盘基准；按已有主题分组展示。</p>
    <p v-if="loading" role="status">正在读取日内标的池…</p>
    <p v-if="error" role="alert" class="pool-error">{{ error }} <button v-if="!settings || conflict" class="secondary-button" :disabled="loading" @click="load">重新读取</button></p>
    <template v-if="settings">
      <div class="pool-versions">
        <div><span>当前配置</span><strong>{{ settings.active ? `${settings.active.member_symbols.length} 个标的` : '尚未建立' }}</strong><small>{{ settings.active?.effective_date ? `自 ${settings.active.effective_date} 生效` : settings.active ? '来自已有采集配置' : '请为下一交易日选择标的' }}</small></div>
        <div><span>待生效配置</span><strong>{{ settings.pending ? `${settings.pending.member_symbols.length} 个标的` : '暂无变更' }}</strong><small>{{ settings.pending ? `${settings.pending.effective_date} 生效（美东交易日）` : '保存后显示生效日期' }}</small></div>
      </div>
      <p v-if="settings.warning" class="pool-warning">{{ settings.warning }}</p>
      <div class="pool-selection-toolbar"><label>搜索 <input v-model="query" type="search" aria-label="搜索日内设置标的或分组" placeholder="代码、名称或分组"></label><span>已选 {{ total }} 个（含固定基准）</span></div>
      <div class="pool-selection-groups">
        <section v-for="group in groups" :key="group.name" class="pool-selection-group">
          <h3>{{ group.name }} <small>{{ group.members.length }}</small></h3>
          <div class="pool-selection-grid"><label v-for="member in group.members" :key="member.symbol" :data-fixed="isFixed(member)">
            <input v-if="isFixed(member)" type="checkbox" checked disabled :aria-label="`${member.symbol} 固定基准`">
            <input v-else v-model="selected" type="checkbox" :value="member.symbol" :disabled="saving || conflict" :aria-label="member.symbol">
            <span><strong>{{ member.symbol }}</strong><small>{{ isFixed(member) ? '固定基准' : member.display_name }}</small></span>
          </label></div>
        </section>
        <p v-if="!groups.length">没有匹配的标的或分组。</p>
      </div>
      <footer><span>{{ dirty ? '有未保存的修改' : '已与保存的配置一致' }}</span><div><button class="secondary-button" :disabled="!dirty || saving" @click="reset">撤销修改</button><button class="primary-button" :disabled="!dirty || saving || conflict || loading" @click="save">{{ saving ? '保存中…' : '保存至下一交易日' }}</button></div></footer>
      <p v-if="notice" role="status" class="pool-success">{{ notice }}</p>
    </template>
  </section>
</template>

<style scoped>
.pool-settings{scroll-margin-top:20px;margin-bottom:26px;padding:20px;border:1px solid var(--line);border-radius:8px;background:var(--surface)}
.pool-settings header,.pool-settings footer,.pool-selection-toolbar{display:flex;align-items:center;justify-content:space-between;gap:14px}
.pool-settings h2{margin:6px 0;font-size:23px}.pool-help,.pool-warning,.pool-error,.pool-success{font-size:12px;line-height:1.7;margin:12px 0;color:var(--muted)}
.pool-error{color:var(--danger)}.pool-success{color:var(--success)}.pool-warning{padding:9px 12px;border-left:2px solid var(--accent);background:var(--surface-raised)}
.pool-versions{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:16px 0}.pool-versions>div{display:grid;gap:6px;padding:12px;border:1px solid var(--line);border-radius:6px}.pool-versions span,.pool-versions small{font-size:11px;color:var(--muted)}.pool-versions strong{font-size:16px}
.pool-selection-toolbar{margin:15px 0;font-size:12px;color:var(--muted)}.pool-selection-toolbar label{display:flex;align-items:center;gap:8px;white-space:nowrap}.pool-selection-toolbar input{max-width:240px;width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:5px;background:var(--surface-raised);color:var(--ink)}
.pool-selection-groups{max-height:390px;overflow:auto;padding-right:5px}.pool-selection-group{margin:0 0 14px}.pool-selection-group h3{font-size:12px;margin:0 0 8px;color:var(--soft-ink)}.pool-selection-group h3 small{color:var(--muted);margin-left:5px}.pool-selection-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:7px}
.pool-selection-grid label{display:flex;gap:8px;align-items:center;padding:9px;border:1px solid var(--line);border-radius:5px;cursor:pointer;min-width:0}.pool-selection-grid label[data-fixed=true]{background:var(--surface-raised)}.pool-selection-grid input{accent-color:var(--accent)}.pool-selection-grid span{display:grid;gap:3px;min-width:0}.pool-selection-grid strong{font:12px monospace}.pool-selection-grid small{font-size:10px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pool-settings footer{border-top:1px solid var(--line);padding-top:14px;margin-top:10px;font-size:11px;color:var(--muted)}.pool-settings footer>div{display:flex;gap:8px}
@media(max-width:800px){.pool-selection-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.pool-settings header,.pool-settings footer,.pool-selection-toolbar{align-items:stretch;flex-direction:column}.pool-settings{padding:14px}.pool-versions{grid-template-columns:1fr}.pool-selection-toolbar input{max-width:none}.pool-selection-toolbar label{width:100%}}
</style>
