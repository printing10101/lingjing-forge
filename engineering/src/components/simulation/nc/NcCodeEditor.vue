<template>
  <div class="content-card">
    <div class="content-card__header">
      <span class="content-card__title">{{
        t("simulationPage.ncCodeTitle")
      }}</span>
      <div class="header-actions">
        <el-upload
          ref="uploadRef"
          :auto-upload="false"
          :show-file-list="false"
          accept=".nc,.NC,.gcode,.gc,.tap,.txt,.CNC,.cnc"
          :on-change="handleFileUpload"
        >
          <el-button size="small" :icon="Upload">
            {{ t("simulationPage.uploadFile") }}
          </el-button>
        </el-upload>
        <el-button
          v-if="gcode"
          size="small"
          :icon="Delete"
          @click="emit('update:gcode', '')"
        >
          {{ t("simulationPage.clear") }}
        </el-button>
      </div>
    </div>
    <div class="content-card__body">
      <el-input
        :model-value="gcode"
        type="textarea"
        :placeholder="t('simulationPage.gcodePlaceholder')"
        :autosize="{ minRows: 6, maxRows: 14 }"
        resize="vertical"
        class="gcode-textarea"
        @input="emit('update:gcode', $event)"
      />
      <div v-if="gcodeStats.lines > 0" class="gcode-stats">
        <span>{{
          t("simulationPage.gcodeLines", { count: gcodeStats.lines })
        }}</span>
        <span>{{
          t("simulationPage.gcodeGCommands", { count: gcodeStats.gCommands })
        }}</span>
        <span>{{
          t("simulationPage.gcodeMCommands", { count: gcodeStats.mCommands })
        }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from "vue";
import { useI18n } from "vue-i18n";
import { Upload, Delete } from "@element-plus/icons-vue";
import type { UploadFile } from "element-plus";
import { ElMessage } from "element-plus";

const { t } = useI18n();

const props = defineProps<{
  gcode: string;
}>();

const emit = defineEmits<{
  "update:gcode": [value: string];
}>();

const uploadRef = ref<InstanceType<
  (typeof import("element-plus"))["ElUpload"]
> | null>(null);

const gcodeStats = computed(() => {
  const text = props.gcode;
  const lines = text
    ? text
        .split("\n")
        .filter(
          (l) =>
            l.trim() && !l.trim().startsWith("(") && !l.trim().startsWith("//"),
        ).length
    : 0;
  const gCommands = (text.match(/[Gg]\d+/g) || []).length;
  const mCommands = (text.match(/[Mm]\d+/g) || []).length;
  return { lines, gCommands, mCommands };
});

function handleFileUpload(file: UploadFile) {
  const reader = new FileReader();
  reader.onload = (e) => {
    const content = e.target?.result as string;
    if (content) {
      emit("update:gcode", content);
      ElMessage.success(
        t("simulationPage.msgFileLoaded", {
          name: file.name,
          lines: gcodeStats.value.lines,
        }),
      );
    }
  };
  reader.onerror = () => {
    ElMessage.error(t("simulationPage.msgFileReadFailed"));
  };
  if (file.raw) {
    reader.readAsText(file.raw);
  }
}
</script>

<style scoped>
.content-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 20px;
  border-bottom: 1px solid var(--bg-200);
}

.content-card__title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.content-card__body {
  padding: 16px 20px;
}

.header-actions {
  display: flex;
  gap: 8px;
}

:deep(.gcode-textarea .el-textarea__inner) {
  font-family: var(--font-mono);
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--text-primary);
}

.gcode-stats {
  display: flex;
  gap: 16px;
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-tertiary);
}
</style>
