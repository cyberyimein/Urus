<script setup lang="ts">
import { RouterLink } from 'vue-router'
</script>

<template>
  <header class="topbar">
    <RouterLink class="brand" to="/">
      <img class="brand-mark" src="/urus-icon.png" alt="" width="32" height="32" />
      <span>
        <strong>Urus</strong>
        <small>systematic research cockpit</small>
      </span>
    </RouterLink>
    <nav class="topnav" aria-label="主导航">
      <RouterLink to="/" active-class="active">首页</RouterLink>
      <RouterLink to="/research" active-class="active">研究中心</RouterLink>
      <RouterLink to="/analysis/new" active-class="active">手动分析</RouterLink>
      <RouterLink to="/research/reports" active-class="active">历史报告</RouterLink>
      <RouterLink to="/settings" active-class="active">设置</RouterLink>
      <RouterLink to="/operations" active-class="active">开发工具</RouterLink>
    </nav>
  </header>
</template>
