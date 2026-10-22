<script setup lang="ts">
defineProps<{ eyebrow: string; title: string; description: string }>()
</script>
<template><div class="page-heading"><div><p class="eyebrow"><span></span>{{ eyebrow }}</p><h1>{{ title }}</h1><p class="page-description">{{ description }}</p></div><div class="heading-actions"><slot /></div></div></template>
