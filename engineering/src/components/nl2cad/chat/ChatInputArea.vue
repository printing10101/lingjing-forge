<template>
  <div class="input-area">
    <div class="input-wrapper">
      <el-input
        :model-value="modelValue"
        type="textarea"
        :rows="2"
        :placeholder="t('nlInputPanel.inputPlaceholder')"
        :disabled="disabled"
        @update:model-value="emit('update:modelValue', $event)"
        @keydown.enter.exact.prevent="emit('send')"
      />
      <el-button
        type="primary"
        :icon="Promotion"
        :loading="disabled"
        :disabled="!modelValue.trim()"
        @click="emit('send')"
      />
    </div>
    <div class="input-hints">
      <el-tag
        size="small"
        type="info"
        @click="fillExample(t('nlInputPanel.exampleBoxPrompt'))"
      >
        {{ t("nlInputPanel.exampleBox") }}
      </el-tag>
      <el-tag
        size="small"
        type="info"
        @click="fillExample(t('nlInputPanel.exampleCylinderPrompt'))"
      >
        {{ t("nlInputPanel.exampleCylinder") }}
      </el-tag>
      <el-tag
        size="small"
        type="info"
        @click="fillExample(t('nlInputPanel.exampleSpherePrompt'))"
      >
        {{ t("nlInputPanel.exampleSphere") }}
      </el-tag>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from "vue-i18n";
import { Promotion } from "@element-plus/icons-vue";

defineOptions({ name: "ChatInputArea" });

defineProps<{
  modelValue: string;
  disabled: boolean;
}>();

const emit = defineEmits<{
  (e: "update:modelValue", value: string): void;
  (e: "send"): void;
}>();

const { t } = useI18n();

function fillExample(text: string) {
  emit("update:modelValue", text);
}
</script>

<style scoped>
.input-area {
  padding: 16px;
  background: var(--bg-card);
  border-top: 1px solid var(--border-light);
}

.input-wrapper {
  display: flex;
  gap: 8px;
  align-items: flex-end;
}

.input-wrapper :deep(.el-textarea__inner) {
  border-radius: var(--radius-lg);
  resize: none;
  box-shadow: var(--shadow-sm);
}

.input-wrapper :deep(.el-button) {
  border-radius: 50%;
  width: 40px;
  height: 40px;
  padding: 0;
}

.input-hints {
  margin-top: 8px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.input-hints .el-tag {
  cursor: pointer;
  transition: all var(--transition-fast);
}

.input-hints .el-tag:hover {
  transform: translateY(-2px);
}
</style>
