<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { api } from '@/api/client'
import type { DatabaseHealthResponse } from '@/types/api'

const databaseHealth = ref<DatabaseHealthResponse | null>(null)
const databaseHealthLoading = ref(true)

const databaseHealthLabel = computed(() => {
  if (databaseHealthLoading.value) return '检查中'
  if (!databaseHealth.value) return '未知'
  if (databaseHealth.value.status === 'ok') return '正常'
  if (databaseHealth.value.status === 'unsupported') return '未检查'
  return '异常'
})

const databaseHealthTitle = computed(() => {
  if (databaseHealthLoading.value) return '正在读取最近一次数据库完整性检查'
  if (!databaseHealth.value) return '无法连接数据库健康检查接口'
  const checkedAt = new Date(databaseHealth.value.checked_at).toLocaleString()
  const message = databaseHealth.value.message ? `：${databaseHealth.value.message}` : ''
  return `最近检查 ${checkedAt}${message}`
})

const databaseHealthTone = computed(() => {
  if (databaseHealthLoading.value) return 'loading'
  if (databaseHealth.value?.status === 'ok') return 'ok'
  if (databaseHealth.value?.status === 'unsupported') return 'unknown'
  return 'error'
})

onMounted(async () => {
  try {
    databaseHealth.value = await api.databaseHealth()
  } catch {
    databaseHealth.value = null
  } finally {
    databaseHealthLoading.value = false
  }
})
</script>

<template>
  <header class="topbar">
    <RouterLink class="brand" to="/">
      <img class="brand-mark" src="/urus-icon.png" alt="" width="32" height="32" />
      <span>
        <strong>Urus</strong>
        <small>evidence decision cockpit</small>
      </span>
    </RouterLink>
    <nav class="topnav" aria-label="主导航">
      <RouterLink to="/" active-class="active">首页</RouterLink>
      <RouterLink to="/groups" active-class="active">组观察</RouterLink>
      <RouterLink to="/indicators" active-class="active">指标扫描</RouterLink>
      <RouterLink to="/strategies" active-class="active">策略扫描</RouterLink>
      <RouterLink to="/observation-runs" active-class="active">收盘观测</RouterLink>
      <RouterLink to="/settings/universe" active-class="active">标的设置</RouterLink>
    </nav>
    <div
      class="database-health"
      :data-tone="databaseHealthTone"
      :title="databaseHealthTitle"
      role="status"
      aria-live="polite"
    >
      <i aria-hidden="true"></i>
      <span>DB {{ databaseHealthLabel }}</span>
    </div>
  </header>
</template>
