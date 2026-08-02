<script setup lang="ts">
import { RouterLink } from 'vue-router'
</script>

<template>
  <header class="topbar">
    <RouterLink class="brand" to="/">
      <span class="brand-mark">U</span>
      <span>
        <strong>Urus</strong>
        <small>stage 1A / data validation</small>
      </span>
    </RouterLink>
    <nav class="topnav" aria-label="主导航">
      <RouterLink to="/" active-class="active">Dashboard</RouterLink>
      <RouterLink to="/runs" active-class="active">Runs</RouterLink>
    </nav>
  </header>
</template>
