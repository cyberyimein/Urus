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
      <RouterLink to="/groups" active-class="active">组观察</RouterLink>
      <RouterLink to="/indicators" active-class="active">指标扫描</RouterLink>
      <RouterLink to="/strategies" active-class="active">策略扫描</RouterLink>
      <RouterLink to="/research" active-class="active">旧研究中心</RouterLink>
      <RouterLink to="/settings" active-class="active">设置</RouterLink>
    </nav>
  </header>
</template>
