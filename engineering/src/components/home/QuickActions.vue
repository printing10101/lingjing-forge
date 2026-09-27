<template>
  <section class="quick-actions">
    <h3 class="section-title">
      {{ t('home.cardQuickActions') }}
    </h3>
    <div class="action-grid">
      <el-button
        v-for="action in quickActions"
        :key="action.label"
        class="action-btn"
        @click="$emit('action-click', action)"
      >
        <el-icon :size="20">
          <component :is="action.icon" />
        </el-icon>
        <span>{{ action.label }}</span>
      </el-button>
    </div>
  </section>
</template>

<script lang="ts">
export interface QuickAction {
  label: string
  icon: object
  route: string
}
</script>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

defineProps<{
  quickActions: QuickAction[]
}>()

defineEmits<{
  'action-click': [action: QuickAction]
}>()

const { t } = useI18n()
</script>

<style scoped>
.section-title {
  margin: 0 0 12px;
  font-size: 0.9375rem;
  font-weight: 600;
  color: var(--text-primary);
}

.action-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}

.action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 46px;
  border-radius: var(--radius-md) !important;
  font-size: 0.875rem;
  font-weight: 500;
  border: 1px solid var(--bg-200) !important;
  background: var(--bg-0) !important;
  color: var(--text-primary) !important;
  transition: all var(--transition-fast);
}

.action-btn:hover {
  border-color: var(--accent-border) !important;
  color: var(--accent-primary) !important;
  background: var(--bg-50) !important;
  box-shadow: var(--shadow-xs);
  transform: translateY(-1px);
}

.action-btn:active {
  transform: translateY(0);
}

@media (max-width: 900px) {
  .action-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>