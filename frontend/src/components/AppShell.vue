<script setup lang="ts">
import { RouterLink } from 'vue-router'
</script>

<template>
  <header class="decision-header">
    <RouterLink class="decision-brand" to="/">
      <span class="decision-brand-mark" aria-hidden="true">U</span>
      <span class="decision-brand-copy">
        <strong>URUS</strong>
        <small>DAILY DECISION HARNESS</small>
      </span>
    </RouterLink>
    <nav class="decision-nav" aria-label="主导航">
      <RouterLink to="/" exact-active-class="active">证据工作台</RouterLink>
      <RouterLink to="/research" active-class="active">研究中心</RouterLink>
      <RouterLink to="/research/reports" active-class="active">历史报告</RouterLink>
      <RouterLink to="/settings" active-class="active">设置</RouterLink>
    </nav>
    <div class="decision-header-meta"><span class="header-live-dot"></span><span>LOCAL</span><b>PHASE A</b></div>
  </header>
</template>
