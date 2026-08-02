<script setup lang="ts">
withDefaults(defineProps<{ label?: string }>(), { label: '模拟数据' })
</script>

<template>
  <span class="mock-badge">{{ label }}</span>
</template>
