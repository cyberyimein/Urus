<script setup lang="ts">
import { statusLabel } from '@/utils/format'

defineProps<{ status: string }>()
</script>

<template>
  <span class="status-badge" :data-status="status">{{ statusLabel(status) }}</span>
</template>

