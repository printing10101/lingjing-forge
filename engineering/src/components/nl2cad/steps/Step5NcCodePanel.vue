<template>
  <div class="content-panel">
    <div class="panel-header">
      <h3>{{ t("workflowGuide.step5Header") }}</h3>
      <p class="hint">
        {{ t("workflowGuide.step5Hint") }}
      </p>
    </div>
    <div class="panel-body">
      <div class="nc-code-container">
        <div v-if="!ncCodeGenerated" class="code-placeholder">
          <el-icon :size="48" class="loading-icon">
            <Loading />
          </el-icon>
          <p>{{ t("workflowGuide.step5Loading") }}</p>
        </div>
        <div v-else class="code-viewer">
          <div class="code-header">
            <span class="code-title">{{ t("workflowGuide.codeTitle") }}</span>
            <div class="code-actions">
              <el-button size="small" @click="$emit('copyCode')">
                <el-icon><DocumentCopy /></el-icon>
                {{ t("workflowGuide.btnCopy") }}
              </el-button>
              <el-button size="small" @click="$emit('downloadCode')">
                <el-icon><Download /></el-icon>
                {{ t("workflowGuide.btnDownload") }}
              </el-button>
            </div>
          </div>
          <pre class="code-content"><code>{{ ncCode }}</code></pre>
        </div>
      </div>
      <div class="panel-actions">
        <el-button @click="$emit('prev')">
          <el-icon><ArrowLeft /></el-icon>
          {{ t("workflowGuide.btnModifyProcess") }}
        </el-button>
        <el-button
          type="primary"
          :disabled="!ncCodeGenerated"
          @click="$emit('next')"
        >
          <el-icon><VideoPlay /></el-icon>
          {{ t("workflowGuide.btnSimulate") }}
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from "vue-i18n";
import {
  ArrowLeft,
  DocumentCopy,
  Download,
  Loading,
  VideoPlay,
} from "@element-plus/icons-vue";

defineProps<{
  ncCodeGenerated: boolean;
  ncCode: string;
}>();

defineEmits<{
  (e: "prev"): void;
  (e: "next"): void;
  (e: "copyCode"): void;
  (e: "downloadCode"): void;
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
.panel-actions {
  display: flex;
  justify-content: space-between;
  margin-top: 24px;
  padding-top: 20px;
  border-top: 1px solid var(--border-light);
}
.nc-code-container {
  min-height: 400px;
}
.code-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 400px;
  color: var(--text-tertiary);
}
.loading-icon {
  animation: spin 1s linear infinite;
}
@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
.code-viewer {
  background: var(--bg-code);
  border-radius: var(--radius-md);
  overflow: hidden;
}
.code-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  background: var(--bg-code-header);
  border-bottom: 1px solid var(--bg-code-border);
}
.code-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-code);
}
.code-actions {
  display: flex;
  gap: 8px;
}
.code-content {
  margin: 0;
  padding: 16px;
  max-height: 400px;
  overflow-y: auto;
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-code-content);
}
</style>
