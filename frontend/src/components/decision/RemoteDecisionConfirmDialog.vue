<script setup lang="ts">
import type { RemoteDecisionPreflight } from '@/types/remoteDecision'

defineProps<{
  modelValue: boolean
  preflight: RemoteDecisionPreflight | null
  title: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  confirm: []
}>()

function shortHash(value: unknown) {
  if (typeof value !== 'string' || !value) return '—'
  return value.length > 20 ? `${value.slice(0, 10)}…${value.slice(-6)}` : value
}
</script>

<template>
  <Teleport to="body">
    <div v-if="modelValue" class="remote-confirm-backdrop" role="presentation" @click.self="emit('update:modelValue', false)">
      <section class="remote-confirm-dialog" role="dialog" aria-modal="true" :aria-label="title">
        <p class="section-kicker">CONFIRM FROZEN EVIDENCE</p>
        <h2>{{ title }}</h2>
        <p>AI 只会读取当前确认的 Observation Run / Dataset，不会更新指标、策略或下单。</p>
        <dl v-if="preflight" class="remote-confirm-summary">
          <div><dt>TRADING DATE</dt><dd>{{ preflight.source_summary.trading_date ?? '—' }}</dd></div>
          <div v-if="preflight.source_summary.previous_trading_date"><dt>PREVIOUS CLOSE</dt><dd>{{ preflight.source_summary.previous_trading_date }} · {{ preflight.source_summary.temporal_context_status ?? 'partial' }}</dd></div>
          <div><dt>SCOPE</dt><dd>{{ preflight.source_summary.scope_id ?? preflight.source_summary.observation_run_id ?? '—' }}</dd></div>
          <div><dt>RUN / SNAPSHOT</dt><dd>{{ preflight.source_summary.observation_run_id ?? '—' }}<span v-if="preflight.source_summary.snapshot_id"> / {{ preflight.source_summary.snapshot_id }}</span></dd></div>
          <div><dt>SYMBOLS</dt><dd>{{ preflight.source_summary.symbol_count ?? '—' }}</dd></div>
          <div><dt>GROUPS</dt><dd>{{ preflight.source_summary.group_count ?? '—' }}</dd></div>
          <div><dt>STRATEGIES</dt><dd>{{ preflight.source_summary.strategy_decision_count ?? '—' }} · errors {{ preflight.source_summary.strategy_error_count ?? 0 }} · N/A {{ preflight.source_summary.strategy_not_applicable_count ?? 0 }}</dd></div>
          <div><dt>FROZEN HASH</dt><dd>{{ shortHash(preflight.source_summary.content_sha256) }}</dd></div>
          <div><dt>INPUT HASH</dt><dd>{{ shortHash(preflight.input_sha256) }}</dd></div>
          <div><dt>WORKFLOW</dt><dd>{{ preflight.binding?.workflow_ref ?? '—' }}</dd></div>
        </dl>
        <ul v-if="preflight?.warnings.length" class="remote-confirm-warnings">
          <li v-for="warning in preflight.warnings" :key="`${warning.code}-${warning.message}`">{{ warning.message }}</li>
        </ul>
        <div class="remote-confirm-actions">
          <button class="secondary-button" type="button" @click="emit('update:modelValue', false)">取消</button>
          <button class="primary-button" type="button" :disabled="!preflight?.enabled" @click="emit('confirm')">确认并运行</button>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.remote-confirm-backdrop { position: fixed; inset: 0; z-index: 1000; display: grid; place-items: center; padding: 24px; background: rgba(8, 15, 28, .58); }
.remote-confirm-dialog { width: min(560px, 100%); padding: 28px; border: 1px solid rgba(129, 152, 189, .35); border-radius: 18px; background: #111b2d; color: #f4f7fd; box-shadow: 0 20px 80px rgba(0, 0, 0, .35); }
.remote-confirm-dialog h2 { margin: 4px 0 10px; }
.remote-confirm-dialog p { color: #b8c4d8; line-height: 1.6; }
.remote-confirm-summary { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin: 20px 0; }
.remote-confirm-summary div { padding: 12px; border-radius: 10px; background: rgba(255,255,255,.05); }
.remote-confirm-summary dt { font-size: 10px; color: #8b9bb5; letter-spacing: .12em; }
.remote-confirm-summary dd { margin: 4px 0 0; font-weight: 600; word-break: break-word; }
.remote-confirm-warnings { margin: 0 0 18px; padding: 10px 10px 10px 28px; border-radius: 10px; background: rgba(218, 166, 82, .1); color: #e5c38c; font-size: 12px; line-height: 1.5; }
.remote-confirm-actions { display: flex; justify-content: flex-end; gap: 10px; }
</style>
