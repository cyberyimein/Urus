<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { ApiError, api } from '@/api/client'
import AppShell from '@/components/AppShell.vue'
import type { RuntimeSettingsResponse, RuntimeSettingsUpdate } from '@/types/settings'

const settings = ref<RuntimeSettingsResponse | null>(null)
const draft = ref<RuntimeSettingsUpdate | null>(null)
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const notice = ref('')
let savedFingerprint = ''

const scheduleRows = [
  {
    key: 'pre_market' as const,
    title: '盘前正式决策',
    eyebrow: '21:30 JST · PRE-MARKET',
    description: '采集盘前证据，并按设置决定是否生成正式 AI 决策。',
  },
  {
    key: 'post_close_review' as const,
    title: '收盘复盘',
    eyebrow: '05:30 JST · POST-CLOSE REVIEW',
    description: '保存收盘后的完整证据，用于当日复盘与 CTA 状态更新。',
  },
  {
    key: 'pre_close' as const,
    title: '尾盘数据采集',
    eyebrow: '04:00 JST · TAIL COLLECTION',
    description: '只采集尾盘数据，固定不启动 AI，也不生成正式决策。',
  },
]

const isDirty = computed(() => {
  if (!draft.value) return false
  return JSON.stringify(draft.value) !== savedFingerprint
})

function cloneDraft(payload: RuntimeSettingsResponse): RuntimeSettingsUpdate {
  return JSON.parse(JSON.stringify({
    revision: payload.revision,
    schedule: payload.schedule,
    models: payload.models,
  })) as RuntimeSettingsUpdate
}

function markSaved(payload: RuntimeSettingsResponse) {
  settings.value = payload
  draft.value = cloneDraft(payload)
  savedFingerprint = JSON.stringify(draft.value)
}

async function loadSettings() {
  loading.value = true
  error.value = ''
  try {
    markSaved(await api.getSettings())
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '无法读取运行设置。'
  } finally {
    loading.value = false
  }
}

async function saveSettings() {
  if (!draft.value || saving.value) return
  saving.value = true
  error.value = ''
  notice.value = ''
  try {
    markSaved(await api.updateSettings(draft.value))
    notice.value = '设置已保存，新的调度周期会按此配置执行。'
  } catch (reason) {
    if (reason instanceof ApiError && reason.status === 409) {
      error.value = '设置版本已变化，请重新读取后再保存。'
    } else {
      error.value = reason instanceof Error ? reason.message : '保存设置失败。'
    }
  } finally {
    saving.value = false
  }
}

function resetDraft() {
  if (settings.value) markSaved(settings.value)
  error.value = ''
  notice.value = ''
}

function formatUpdatedAt(value: string | null | undefined) {
  if (!value) return '尚未写入运行时覆盖'
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}

onMounted(loadSettings)
</script>

<template>
  <AppShell />
  <main class="page-shell settings-page">
    <header class="settings-header">
      <div>
        <p class="eyebrow">SYSTEM SETTINGS · RUNTIME CONTROL</p>
        <h1>运行设置</h1>
        <p class="settings-intro">控制两次正式日程是否运行、是否只采集不启动 AI，以及本分支实际使用的模型入口。</p>
      </div>
      <div v-if="settings" class="settings-status">
        <RouterLink class="secondary-button" to="/settings/universe">大盘 / ETF / 个股设置</RouterLink>
        <span class="settings-source" :data-source="settings.source">
          {{ settings.source === 'runtime' ? '运行时覆盖' : '环境默认' }}
        </span>
        <span>版本 {{ settings.revision }}</span>
        <span>{{ formatUpdatedAt(settings.updated_at) }}</span>
      </div>
    </header>

    <div v-if="loading" class="settings-loading">正在读取当前运行设置…</div>
    <div v-else-if="error && !draft" class="error-banner" role="alert">
      {{ error }}
      <button class="secondary-button settings-retry" type="button" @click="loadSettings">重新读取</button>
    </div>

    <template v-else-if="draft && settings">
      <section class="settings-section" aria-labelledby="schedule-title">
        <div class="settings-section-heading">
          <div>
            <p class="eyebrow">01 · SCHEDULE POLICY</p>
            <h2 id="schedule-title">日程任务</h2>
          </div>
          <p class="settings-section-note">修改后由调度器在下一个到期槽位读取；不需要重启后端。</p>
        </div>

        <div class="schedule-list">
          <article v-for="row in scheduleRows" :key="row.key" class="schedule-card" :data-disabled="!draft.schedule[row.key].enabled">
            <div class="schedule-card-copy">
              <p class="schedule-eyebrow">{{ row.eyebrow }}</p>
              <h3>{{ row.title }}</h3>
              <p>{{ row.description }}</p>
            </div>
            <div class="schedule-controls">
              <label class="switch-control">
                <input v-model="draft.schedule[row.key].enabled" type="checkbox" />
                <span class="switch-track" aria-hidden="true"><span></span></span>
                <span>{{ draft.schedule[row.key].enabled ? '执行任务' : '已停用' }}</span>
              </label>
              <label class="switch-control ai-control" :data-fixed="row.key === 'pre_close'">
                <input
                  type="checkbox"
                  :checked="!draft.schedule[row.key].skip_ai_decision"
                  :disabled="row.key === 'pre_close'"
                  @change="draft.schedule[row.key].skip_ai_decision = !(($event.target as HTMLInputElement).checked)"
                />
                <span class="switch-track" aria-hidden="true"><span></span></span>
                <span>{{ draft.schedule[row.key].skip_ai_decision ? 'AI 已关闭（只采集）' : 'AI 已启用' }}</span>
              </label>
            </div>
          </article>
        </div>

        <div class="settings-callout">
          <strong>当前边界</strong>
          <span>尾盘槽位永远只负责冻结数据；AI 决策只可能发生在盘前或收盘复盘，且可以分别关闭。</span>
        </div>
      </section>

      <section class="settings-section" aria-labelledby="model-title">
        <div class="settings-section-heading">
          <div>
            <p class="eyebrow">02 · MODEL ROUTING</p>
            <h2 id="model-title">模型入口</h2>
          </div>
          <p class="settings-section-note">保存模型标识与计费单价，不保存任何密钥。</p>
        </div>

        <div class="pricing-grid">
          <label class="model-field pricing-field">
            <span class="model-field-label">输入价格</span>
            <div class="price-input"><span>$</span><input v-model.number="draft.models.input_cost_per_million" type="number" min="0" step="0.000001" /><em>/ 1M tokens</em></div>
            <small>普通 prompt token 的每百万美元价格。</small>
          </label>
          <label class="model-field pricing-field">
            <span class="model-field-label">缓存读取价格</span>
            <div class="price-input"><span>$</span><input v-model.number="draft.models.cached_input_cost_per_million" type="number" min="0" step="0.000001" /><em>/ 1M tokens</em></div>
            <small>OpenRouter usage 中 cached_tokens 命中部分的价格。</small>
          </label>
          <label class="model-field pricing-field">
            <span class="model-field-label">缓存写入价格</span>
            <div class="price-input"><span>$</span><input v-model.number="draft.models.cache_write_cost_per_million" type="number" min="0" step="0.000001" /><em>/ 1M tokens</em></div>
            <small>写入 prompt cache 的每百万美元价格。</small>
          </label>
          <label class="model-field pricing-field">
            <span class="model-field-label">输出价格</span>
            <div class="price-input"><span>$</span><input v-model.number="draft.models.output_cost_per_million" type="number" min="0" step="0.000001" /><em>/ 1M tokens</em></div>
            <small>completion token 的每百万美元价格。</small>
          </label>
        </div>

        <div class="settings-callout">
          <strong>费用口径</strong>
          <span>估算费用 = 普通输入 × 输入价格 + 缓存读取 × 读取价格 + 缓存写入 × 写入价格 + 输出 × 输出价格。所有价格为 0 时只记录 token。</span>
        </div>

        <div class="model-grid">
          <label class="model-field">
            <span class="model-field-label">AI 决策模型</span>
            <input v-model="draft.models.ai_decision_model" type="text" spellcheck="false" autocomplete="off" />
            <small>当前通过 OpenRouter 调用。填写 provider/model 标识，例如 deepseek/...。</small>
          </label>
          <label class="model-field">
            <span class="model-field-label">Anomalo 检索 Agent（预设）</span>
            <input v-model="draft.models.anomalo_retrieval_agent" type="text" spellcheck="false" autocomplete="off" />
            <small>{{ settings.notes.anomalo_model_note }}</small>
          </label>
        </div>

        <div class="model-routing-note">
          <span class="routing-mark">↗</span>
          <div>
            <strong>Anomalo 的实际底层模型不由 Urus 请求参数选择</strong>
            <p>这里显示并切换的是 Anomalo 预设 Agent 名称；预设内部使用哪个检索模型，由 Anomalo 端配置决定。这样不会把一个无法验证的模型名称写进报告。</p>
          </div>
        </div>
        <div class="runtime-capability" :data-ready="settings.capabilities.ai_decision_enabled && settings.capabilities.openrouter_configured">
          <span class="capability-dot" aria-hidden="true"></span>
          <span v-if="settings.capabilities.ai_decision_enabled && settings.capabilities.openrouter_configured">AI 决策运行时已启用，可按上方日程开关调用。</span>
          <span v-else-if="!settings.capabilities.ai_decision_enabled">AI 决策运行时由环境配置关闭；当前日程只会采集数据。</span>
          <span v-else>AI 决策已开启，但 OpenRouter 凭据未配置；启动 AI 的任务会失败。</span>
        </div>
      </section>

      <div v-if="error" class="error-banner settings-feedback" role="alert">{{ error }}</div>
      <div v-if="notice" class="settings-success" role="status">{{ notice }}</div>
      <footer class="settings-actions">
        <span class="settings-dirty">{{ isDirty ? '有未保存修改' : '设置已同步' }}</span>
        <div>
          <button class="secondary-button" type="button" :disabled="!isDirty || saving" @click="resetDraft">撤销修改</button>
          <button class="primary-button" type="button" :disabled="!isDirty || saving" @click="saveSettings">{{ saving ? '保存中…' : '保存运行设置' }}</button>
        </div>
      </footer>
    </template>
  </main>
</template>

<style scoped>
.settings-page { max-width: 1000px; }
.settings-header { display: flex; align-items: end; justify-content: space-between; gap: 30px; padding: 39px 0 28px; border-top: 1px solid var(--line); }
.settings-header h1 { margin-top: 10px; font-size: clamp(34px, 5vw, 52px); }
.settings-intro { max-width: 600px; margin-top: 15px; color: var(--soft-ink); font-size: 14px; line-height: 1.6; }
.settings-status { display: grid; justify-items: end; gap: 5px; color: var(--muted); font: 10px "SFMono-Regular", Consolas, monospace; white-space: nowrap; }
.settings-source { padding: 5px 7px; border: 1px solid var(--line); border-radius: 4px; color: var(--muted); }
.settings-source[data-source="runtime"] { border-color: #8f4d3d; color: var(--success); }
.settings-loading { padding: 25px 0; border-top: 1px solid var(--line-soft); color: var(--muted); font: 11px "SFMono-Regular", Consolas, monospace; }
.settings-retry { margin-left: 12px; min-height: 32px; }
.settings-section { margin-top: 36px; padding-top: 25px; border-top: 1px solid var(--line); }
.settings-section-heading { display: flex; align-items: end; justify-content: space-between; gap: 20px; margin-bottom: 18px; }
.settings-section-heading h2 { margin-top: 7px; font-size: 26px; }
.settings-section-note { max-width: 360px; color: var(--muted); font: 10px/1.5 "SFMono-Regular", Consolas, monospace; text-align: right; }
.schedule-list { display: grid; gap: 9px; }
.schedule-card { display: grid; grid-template-columns: minmax(0, 1fr) minmax(310px, 360px); align-items: center; gap: 25px; padding: 18px 19px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); }
.schedule-card[data-disabled="true"] { opacity: .62; }
.schedule-eyebrow { color: var(--muted); font: 9px "SFMono-Regular", Consolas, monospace; letter-spacing: .06em; }
.schedule-card h3 { margin-top: 7px; font-size: 18px; font-weight: 500; }
.schedule-card-copy > p:last-child { margin-top: 7px; color: var(--soft-ink); font-size: 12px; line-height: 1.45; }
.schedule-controls { display: grid; gap: 9px; }
.switch-control { display: flex; align-items: center; gap: 9px; color: var(--soft-ink); font: 11px "SFMono-Regular", Consolas, monospace; cursor: pointer; }
.switch-control input { position: absolute; width: 1px; height: 1px; overflow: hidden; opacity: 0; pointer-events: none; }
.switch-track { display: inline-flex; width: 33px; height: 19px; align-items: center; padding: 2px; border: 1px solid var(--line); border-radius: 999px; background: var(--surface-raised); transition: .15s ease; }
.switch-track span { width: 13px; height: 13px; border-radius: 50%; background: var(--muted); transition: .15s ease; }
.switch-control input:checked + .switch-track { border-color: var(--accent); background: rgba(182, 79, 56, .25); }
.switch-control input:checked + .switch-track span { transform: translateX(14px); background: var(--accent); }
.switch-control input:focus-visible + .switch-track { outline: 2px solid var(--accent); outline-offset: 2px; }
.switch-control[data-fixed="true"] { color: var(--muted); cursor: not-allowed; }
.switch-control[data-fixed="true"] .switch-track { border-style: dashed; }
.settings-callout, .model-routing-note { display: flex; gap: 12px; margin-top: 13px; padding: 12px 14px; border: 1px solid var(--line-soft); border-radius: 7px; color: var(--soft-ink); background: rgba(42, 33, 28, .55); font-size: 12px; line-height: 1.5; }
.settings-callout strong { color: var(--accent); font: 10px "SFMono-Regular", Consolas, monospace; white-space: nowrap; }
.model-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.pricing-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-top: 10px; }
.price-input { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 7px; color: var(--muted); font: 10px "SFMono-Regular", Consolas, monospace; }
.price-input em { font-style: normal; white-space: nowrap; }
.model-field { display: grid; gap: 8px; padding: 16px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); }
.model-field-label { color: var(--ink); font-size: 14px; }
.model-field input { width: 100%; min-height: 39px; padding: 0 10px; border: 1px solid var(--line); border-radius: 5px; color: var(--ink); background: var(--surface-raised); outline: none; font: 11px "SFMono-Regular", Consolas, monospace; }
.model-field input:focus { border-color: var(--accent); box-shadow: 0 0 0 2px rgba(182, 79, 56, .14); }
.model-field small { color: var(--muted); font-size: 11px; line-height: 1.45; }
.model-routing-note { margin-top: 10px; border-color: #6f6846; }
.routing-mark { color: var(--accent); font-size: 18px; }
.model-routing-note strong { color: var(--ink); font-size: 12px; font-weight: 500; }
.model-routing-note p { margin-top: 5px; color: var(--muted); font-size: 11px; line-height: 1.5; }
.runtime-capability { display: flex; align-items: center; gap: 8px; margin-top: 10px; color: var(--muted); font: 10px/1.5 "SFMono-Regular", Consolas, monospace; }
.runtime-capability[data-ready="true"] { color: var(--success); }
.capability-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--danger); }
.runtime-capability[data-ready="true"] .capability-dot { background: var(--success); }
.settings-feedback { margin-top: 26px; }
.settings-success { margin-top: 26px; padding: 11px 13px; border: 1px solid #8f4d3d; border-radius: 7px; color: var(--success); background: rgba(196, 122, 80, .08); font-size: 13px; }
.settings-actions { display: flex; align-items: center; justify-content: space-between; gap: 15px; margin-top: 21px; padding-top: 18px; border-top: 1px solid var(--line); }
.settings-actions > div { display: flex; gap: 8px; }
.settings-dirty { color: var(--muted); font: 10px "SFMono-Regular", Consolas, monospace; }
@media (max-width: 760px) {
  .settings-header, .settings-section-heading, .settings-actions { align-items: start; flex-direction: column; }
  .settings-status { justify-items: start; }
  .settings-section-note { text-align: left; }
  .schedule-card { grid-template-columns: 1fr; gap: 16px; }
  .model-grid, .pricing-grid { grid-template-columns: 1fr; }
  .settings-actions { gap: 12px; }
  .settings-actions > div { width: 100%; }
  .settings-actions button { flex: 1; }
}
</style>
