<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { useRouter } from 'vue-router'

import { api } from '@/api/client'
import AppShell from '@/components/AppShell.vue'

const router = useRouter()
const submitting = ref(false)
const error = ref('')
const checkingRuntime = ref(true)
const aiEnabled = ref(false)
const runtimeMessage = ref('正在检查 AI 运行状态…')

const canStart = computed(() => aiEnabled.value && !checkingRuntime.value && !submitting.value)

async function checkRuntime() {
  checkingRuntime.value = true
  try {
    const settings = await api.getSettings()
    aiEnabled.value = settings.capabilities.ai_decision_enabled && settings.capabilities.openrouter_configured
    runtimeMessage.value = aiEnabled.value
      ? `AI 已就绪 · ${settings.models.ai_decision_model}`
      : !settings.capabilities.ai_decision_enabled
        ? 'AI 运行时未启用，请先到运行设置启用后端 AI。'
        : 'OpenRouter 凭据未配置，无法生成 AI 现状分析。'
  } catch {
    aiEnabled.value = false
    runtimeMessage.value = '无法确认 AI 状态，请检查后端连接。'
  } finally {
    checkingRuntime.value = false
  }
}

async function startAnalysis() {
  if (!canStart.value) return
  submitting.value = true
  error.value = ''
  try {
    const result = await api.createManualAnalysis()
    await router.push(`/analysis/runs/${result.run_id}`)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '无法发起手动分析。'
    submitting.value = false
  }
}

onMounted(checkRuntime)
</script>

<template>
  <AppShell />
  <main class="page-shell analysis-launch-page">
    <RouterLink class="back-link" to="/">← 返回首页</RouterLink>
    <section class="analysis-confirm-card">
      <p class="eyebrow">ON-DEMAND CURRENT STATE</p>
      <h1>立即分析当前市场</h1>
      <p class="hero-copy">拉取当前行情并生成一份即时分析报告。当前完整 Universe 通常需要 8–20 分钟。</p>

      <dl class="analysis-summary">
        <div>
          <dt>采集范围</dt>
          <dd>市场、CTA、个股与期权</dd>
        </div>
        <div>
          <dt>报告内容</dt>
          <dd>技术报告 + AI 现状分析</dd>
        </div>
        <div>
          <dt>用途</dt>
          <dd>辅助当前判断，不计入正式预测与复盘评分</dd>
        </div>
      </dl>

      <div class="analysis-runtime-note" :data-ready="aiEnabled">
        <span class="status-dot" aria-hidden="true"></span>
        <span>{{ runtimeMessage }}</span>
        <RouterLink v-if="!checkingRuntime && !aiEnabled" to="/settings">打开运行设置</RouterLink>
      </div>
      <div v-if="error" class="error-banner" role="alert">{{ error }}</div>
      <div class="analysis-launch-actions">
        <RouterLink class="analysis-cancel-link" to="/">取消</RouterLink>
        <button class="primary-button" type="button" :disabled="!canStart" @click="startAnalysis">{{ submitting ? '正在创建任务…' : checkingRuntime ? '检查 AI 状态…' : '开始分析' }}</button>
      </div>
    </section>
  </main>
</template>
