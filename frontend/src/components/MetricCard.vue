<script setup lang="ts">
defineProps<{ label: string; value: string; unit?: string; detail: string; tone?: 'blue' | 'cyan' | 'amber'; badge?: string }>()
</script>
<template><article class="metric-card" :class="tone || 'blue'"><div class="metric-label">{{ label }}<span class="metric-badge">{{ badge || 'REAL DATA' }}</span></div><div class="metric-value">{{ value }}<span v-if="unit">{{ unit }}</span></div><p>{{ detail }}</p><span class="metric-decoration" aria-hidden="true"></span></article></template>
