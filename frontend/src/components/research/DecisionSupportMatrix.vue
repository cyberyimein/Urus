<script setup lang="ts">
import { computed } from 'vue'

import type { DecisionReport } from '@/types/research'
import { evidence, list, record, text } from './reportHelpers'

type SupportItem = { label: string; path?: string }

const props = defineProps<{
  report: DecisionReport
}>()

const emit = defineEmits<{
  (event: 'focus-evidence', path: string): void
}>()

const regime = computed(() => record(props.report.market_regime))

function asItems(value: unknown): SupportItem[] {
  if (!Array.isArray(value)) return []
  return value.map((item) => {
    if (item && typeof item === 'object') {
      const entry = item as Record<string, unknown>
      return { label: text(entry.observation ?? entry.reason ?? entry.label ?? entry.text), path: typeof entry.path === 'string' ? entry.path : undefined }
    }
    return { label: String(item) }
  }).filter((item) => item.label !== '—')
}

const marketEvidence = computed(() => evidence(regime.value.evidence).map((item) => ({ label: item.observation || '市场证据', path: item.path })))
const supportItems = computed<SupportItem[]>(() => [
  ...asItems(regime.value.supporting_factors ?? regime.value.support ?? regime.value.confirmation_conditions),
  ...marketEvidence.value,
].slice(0, 4))
const opposeItems = computed<SupportItem[]>(() => [
  ...asItems(regime.value.contradicting_factors ?? regime.value.opposing_factors ?? regime.value.risks ?? regime.value.invalidation_conditions),
  ...list(props.report.portfolio_warnings).map((label): SupportItem => ({ label })),
].slice(0, 4))

const hasExtraEvidence = computed(() => marketEvidence.value.length > supportItems.value.length)
</script>

<template>
  <section class="decision-support-section">
    <div class="decision-section-heading">
      <div>
        <p class="eyebrow">EVIDENCE BALANCE</p>
        <h2>支持与反对</h2>
      </div>
      <span class="subtle">只显示影响当前判断的前四项</span>
    </div>

    <div class="decision-support-grid">
      <article class="decision-support-column" data-tone="support">
        <div class="decision-support-title"><span aria-hidden="true">+</span><strong>支持当前判断</strong></div>
        <ul v-if="supportItems.length">
          <li v-for="item in supportItems" :key="`${item.path ?? item.label}-support`">
            <button v-if="item.path" type="button" @click="emit('focus-evidence', item.path)">{{ item.label }}</button>
            <span v-else>{{ item.label }}</span>
          </li>
        </ul>
        <p v-else class="decision-empty-copy">当前输出没有单独声明支持因素。</p>
      </article>

      <article class="decision-support-column" data-tone="oppose">
        <div class="decision-support-title"><span aria-hidden="true">!</span><strong>可能改变判断</strong></div>
        <ul v-if="opposeItems.length">
          <li v-for="item in opposeItems" :key="`${item.path ?? item.label}-oppose`">
            <button v-if="item.path" type="button" @click="emit('focus-evidence', item.path)">{{ item.label }}</button>
            <span v-else>{{ item.label }}</span>
          </li>
        </ul>
        <p v-else class="decision-empty-copy">当前输出没有单独声明反向因素。</p>
      </article>
    </div>

    <p v-if="hasExtraEvidence" class="decision-support-footnote">完整市场证据仍可从技术报告和工作流验证中查看。</p>
  </section>
</template>
