<template>
  <div class="content-panel">
    <div class="panel-header">
      <h3>{{ t("workflowGuide.step1Header") }}</h3>
      <p class="hint">
        {{ t("workflowGuide.step1Hint") }}
      </p>
    </div>
    <div class="panel-body">
      <div class="example-cards">
        <div
          v-for="example in examples"
          :key="example.text"
          class="example-card"
          @click="$emit('fillExample', example.text)"
        >
          <div class="example-icon">
            <el-icon><Document /></el-icon>
          </div>
          <div class="example-text">
            {{ example.text }}
          </div>
        </div>
      </div>
      <div class="input-section">
        <el-input
          :model-value="modelValue"
          type="textarea"
          :rows="4"
          :placeholder="t('workflowGuide.step1Placeholder')"
          class="nl-input"
          @update:model-value="$emit('update:modelValue', $event)"
        />
        <div class="input-actions">
          <el-button
            type="primary"
            :disabled="!modelValue?.trim()"
            @click="$emit('next')"
          >
            <el-icon><ArrowRight /></el-icon>
            {{ t("workflowGuide.btnNext") }}
          </el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from "vue-i18n";
import { ArrowRight, Document } from "@element-plus/icons-vue";

export interface ExampleItem {
  text: string;
}

defineProps<{
  examples: ExampleItem[];
  modelValue?: string;
}>();

defineEmits<{
  (e: "update:modelValue", value: string): void;
  (e: "fillExample", text: string): void;
  (e: "next"): void;
}>();

const { t } = useI18n();
</script>

<style scoped>
.content-panel {
  max-width: 900px;
  margin: 0 auto;
}
.panel-header {
  margin-bottom: 24px;
}
.panel-header h3 {
  font-size: 20px;
  font-weight: 700;
  color: var(--text-primary);
  margin: 0 0 8px;
}
.panel-header .hint {
  font-size: 14px;
  color: var(--text-tertiary);
  margin: 0;
}
.panel-body {
  background: var(--bg-card);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-lg);
  padding: 24px;
}
.example-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 12px;
  margin-bottom: 24px;
}
.example-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px;
  background: var(--bg-secondary);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--transition-normal);
}
.example-card:hover {
  background: var(--bg-tertiary);
  border-color: var(--accent-primary);
  transform: translateY(-2px);
}
.example-icon {
  width: 36px;
  height: 36px;
  border-radius: var(--radius-md);
  background: var(--accent-light);
  color: var(--accent-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.example-text {
  font-size: 13px;
  color: var(--text-primary);
  line-height: 1.5;
}
.input-section {
  margin-top: 20px;
}
.nl-input :deep(.el-textarea__inner) {
  font-size: 14px;
  line-height: 1.6;
  resize: none;
}
.input-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
