<template>
  <section class="time-filter">
    <div class="filter-group">
      <button
        v-for="range in timeRanges"
        :key="range.key"
        :class="['filter-btn', { active: activeRange === range.key }]"
        @click="$emit('update:activeRange', range.key)"
      >
        {{ range.label }}
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
defineProps<{
  activeRange: string
  timeRanges: Array<{ key: string; label: string }>
}>()

defineEmits<{
  'update:activeRange': [value: string]
}>()
</script>

<style scoped>
.time-filter {
  display: flex;
  justify-content: flex-end;
}

.filter-group {
  display: flex;
  gap: 0;
  background: var(--bg-tertiary);
  border-radius: var(--radius-md);
  padding: 3px;
}

.filter-btn {
  padding: 7px 22px;
  border: none;
  background: transparent;
  border-radius: calc(var(--radius-md) - 2px);
  font-size: 0.825rem;
  font-weight: 500;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all var(--transition-fast);
  font-family: var(--font-sans);
}

.filter-btn.active {
  background: var(--bg-0);
  color: var(--text-primary);
  box-shadow: var(--shadow-xs);
  font-weight: 600;
}

.filter-btn:not(.active):hover {
  color: var(--text-primary);
}
</style>