<template>
  <div class="steps-indicator">
    <div
      v-for="(step, index) in steps"
      :key="step.id"
      class="step-item"
      :class="{
        'is-active': currentStep === index,
        'is-completed': currentStep > index,
        'is-clickable': step.clickable
      }"
      @click="$emit('stepClick', index)"
    >
      <div class="step-icon">
        <el-icon v-if="currentStep > index"><Check /></el-icon>
        <span v-else>{{ index + 1 }}</span>
      </div>
      <div class="step-info">
        <div class="step-title">{{ step.title }}</div>
        <div class="step-desc">{{ step.description }}</div>
      </div>
      <div v-if="index < steps.length - 1" class="step-connector" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { Check } from '@element-plus/icons-vue'

interface WorkflowStep {
  id: string
  title: string
  description: string
  clickable?: boolean
}

defineProps<{ steps: WorkflowStep[]; currentStep: number }>()
defineEmits<{ stepClick: [index: number] }>()
</script>

<style scoped>
.steps-indicator { display: flex; align-items: flex-start; gap: 0; margin-bottom: 24px; overflow-x: auto; }
.step-item { display: flex; align-items: flex-start; gap: 8px; position: relative; flex: 1; min-width: 120px; cursor: default; }
.step-item.is-clickable { cursor: pointer; }
.step-icon { width: 28px; height: 28px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 13px; font-weight: 600; background: var(--el-fill-color-darker); color: var(--el-text-color-secondary); border: 2px solid var(--el-border-color-light); flex-shrink: 0; }
.step-item.is-active .step-icon { background: var(--accent-primary); color: #fff; border-color: var(--accent-primary); }
.step-item.is-completed .step-icon { background: var(--state-success); color: #fff; border-color: var(--state-success); }
.step-title { font-size: 13px; font-weight: 500; color: var(--el-text-color-primary); }
.step-desc { font-size: 11px; color: var(--el-text-color-placeholder); margin-top: 2px; }
.step-connector { position: absolute; left: 40px; top: 14px; width: calc(100% - 0px); height: 2px; background: var(--el-border-color-light); }
.is-completed .step-connector { background: var(--state-success); }
</style>
