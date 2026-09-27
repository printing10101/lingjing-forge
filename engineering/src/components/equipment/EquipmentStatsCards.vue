<template>
  <div class="stats-row">
    <div
      v-for="card in cards"
      :key="card.label"
      class="stat-card"
      :class="'stat-card--' + card.type"
    >
      <div class="stat-card__icon">
        <el-icon :size="24">
          <component :is="card.icon" />
        </el-icon>
      </div>
      <div class="stat-card__content">
        <span class="stat-card__value">{{ card.value }}</span>
        <span class="stat-card__label">{{ card.label }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { Component } from 'vue'

export interface StatsCard {
  label: string
  value: number
  icon: Component
  type: string
}

defineProps<{
  cards: StatsCard[]
}>()
</script>