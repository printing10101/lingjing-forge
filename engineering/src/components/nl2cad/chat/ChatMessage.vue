<template>
  <div v-if="message.role === 'user'" class="message user-message">
    <div class="message-content">
      <div class="message-bubble user-bubble">
        {{ message.content }}
      </div>
      <div class="message-time">
        {{ formatTime(message.timestamp) }}
      </div>
    </div>
    <div class="message-avatar user-avatar">
      <el-icon><User /></el-icon>
    </div>
  </div>

  <div v-else class="message assistant-message">
    <div class="message-avatar">
      <el-icon><ChatDotRound /></el-icon>
    </div>
    <div class="message-content">
      <!-- 参数提取结果 -->
      <div v-if="message.type === 'params'" class="message-bubble">
        <p>{{ t("nlInputPanel.paramsExtracted") }}</p>
        <div class="params-card">
          <div class="param-row">
            <span class="param-label">{{
              t("nlInputPanel.shapeTypeLabel")
            }}</span>
            <span class="param-value">{{
              getShapeLabel(message.params?.shape_type)
            }}</span>
          </div>
          <div v-if="message.params?.dimensions" class="param-row">
            <span class="param-label">{{
              t("nlInputPanel.dimensionsLabel")
            }}</span>
            <span class="param-value">
              <template v-if="message.params.dimensions.length">
                {{ t("nlInputPanel.dimLength") }}
                {{ message.params.dimensions.length }}mm
              </template>
              <template v-if="message.params.dimensions.width">
                × {{ t("nlInputPanel.dimWidth") }}
                {{ message.params.dimensions.width }}mm
              </template>
              <template v-if="message.params.dimensions.height">
                × {{ t("nlInputPanel.dimHeight") }}
                {{ message.params.dimensions.height }}mm
              </template>
              <template v-if="message.params.dimensions.radius">
                {{ t("nlInputPanel.dimRadius") }}
                {{ message.params.dimensions.radius }}mm
              </template>
            </span>
          </div>
          <div v-if="message.params?.features?.length" class="param-row">
            <span class="param-label">{{
              t("nlInputPanel.featuresLabel")
            }}</span>
            <span class="param-value">
              {{
                message.params.features
                  .map((f) => getFeatureLabel(f.type))
                  .join(", ")
              }}
            </span>
          </div>
          <div v-if="message.params?.material" class="param-row">
            <span class="param-label">{{
              t("nlInputPanel.materialLabel")
            }}</span>
            <span class="param-value">{{ message.params.material }}</span>
          </div>
          <div class="param-row confidence-row">
            <span class="param-label">{{
              t("nlInputPanel.confidenceLabel")
            }}</span>
            <el-progress
              :percentage="
                Math.round((message.params?.confidence || 0.8) * 100)
              "
              :color="getConfidenceColor(message.params?.confidence || 0.8)"
              :stroke-width="8"
              style="flex: 1; margin-left: 8px"
            />
          </div>
        </div>
        <div class="message-actions">
          <el-button
            type="primary"
            size="small"
            @click="emit('confirm-params', message.params)"
          >
            <el-icon><Check /></el-icon>{{ t("nlInputPanel.confirmGenerate") }}
          </el-button>
          <el-button size="small" @click="emit('edit-params', message.params)">
            <el-icon><Edit /></el-icon>{{ t("nlInputPanel.editParams") }}
          </el-button>
        </div>
      </div>

      <!-- 模型生成结果 -->
      <div v-else-if="message.type === 'model'" class="message-bubble">
        <p>{{ t("nlInputPanel.modelGenerated") }}</p>
        <div class="model-card">
          <div class="model-preview">
            <el-icon :size="32">
              <Box />
            </el-icon>
          </div>
          <div class="model-info">
            <div class="model-name">
              {{ message.modelName || t("nlInputPanel.defaultModelName") }}
            </div>
            <div class="model-format">
              {{ message.format?.toUpperCase() }}
            </div>
          </div>
        </div>
        <div class="message-actions">
          <el-button
            type="primary"
            size="small"
            @click="emit('view-3d', message.modelPath)"
          >
            <el-icon><View /></el-icon>{{ t("nlInputPanel.viewIn3D") }}
          </el-button>
          <el-button size="small" @click="emit('download', message.modelPath)">
            <el-icon><Download /></el-icon>{{ t("nlInputPanel.download") }}
          </el-button>
        </div>
      </div>

      <!-- 普通文本消息 -->
      <div v-else class="message-bubble">
        {{ message.content }}
      </div>
      <div class="message-time">
        {{ formatTime(message.timestamp) }}
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from "vue-i18n";
import {
  ChatDotRound,
  User,
  Check,
  Edit,
  View,
  Download,
  Box,
} from "@element-plus/icons-vue";
import type { Message } from "./types";

defineOptions({ name: "ChatMessage" });

defineProps<{
  message: Message;
}>();

const emit = defineEmits<{
  (e: "confirm-params", params: Message["params"]): void;
  (e: "edit-params", params: Message["params"]): void;
  (e: "view-3d", modelPath: string | undefined): void;
  (e: "download", modelPath: string | undefined): void;
}>();

const { t } = useI18n();

function formatTime(date: Date): string {
  return date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function getShapeLabel(type: string | undefined): string {
  if (!type) return "";
  const map: Record<string, string> = {
    box: t("nlInputPanel.shapeBox"),
    cylinder: t("nlInputPanel.shapeCylinder"),
    sphere: t("nlInputPanel.shapeSphere"),
    cone: t("nlInputPanel.shapeCone"),
  };
  return map[type] || type;
}

function getFeatureLabel(type: string): string {
  const map: Record<string, string> = {
    chamfer: t("nlInputPanel.featureChamfer"),
    fillet: t("nlInputPanel.featureFillet"),
    hole: t("nlInputPanel.featureHole"),
    slot: t("nlInputPanel.featureSlot"),
  };
  return map[type] || type;
}

function getConfidenceColor(confidence: number): string {
  if (confidence >= 0.8) return "var(--success)";
  if (confidence >= 0.6) return "var(--warning)";
  return "var(--error)";
}
</script>

<style scoped>
.message {
  display: flex;
  gap: 12px;
  max-width: 85%;
}

.user-message {
  align-self: flex-end;
  flex-direction: row-reverse;
}

.assistant-message {
  align-self: flex-start;
}

.message-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  background: linear-gradient(
    135deg,
    var(--brand-500) 0%,
    var(--brand-600) 100%
  );
  color: white;
  font-size: 18px;
}

.user-avatar {
  background: linear-gradient(135deg, var(--success) 0%, var(--info) 100%);
}

.message-content {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.message-bubble {
  padding: 12px 16px;
  border-radius: var(--radius-lg);
  background: var(--bg-card);
  box-shadow: var(--shadow-sm);
  font-size: 14px;
  line-height: 1.6;
  color: var(--text-primary);
}

.user-bubble {
  background: linear-gradient(
    135deg,
    var(--brand-500) 0%,
    var(--brand-600) 100%
  );
  color: white;
}

.message-time {
  font-size: 11px;
  color: var(--text-tertiary);
  padding: 0 4px;
}

/* 参数卡片 */
.params-card {
  margin-top: 12px;
  padding: 12px;
  background: var(--bg-secondary);
  border-radius: var(--radius-md);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.param-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.param-label {
  font-weight: 500;
  color: var(--text-secondary);
  min-width: 60px;
}

.param-value {
  color: var(--text-primary);
}

.confidence-row {
  margin-top: 4px;
  padding-top: 8px;
  border-top: 1px solid var(--border-light);
}

.message-actions {
  margin-top: 12px;
  display: flex;
  gap: 8px;
}

/* 模型卡片 */
.model-card {
  margin-top: 12px;
  padding: 12px;
  background: var(--bg-secondary);
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  gap: 12px;
}

.model-preview {
  width: 48px;
  height: 48px;
  border-radius: var(--radius-md);
  background: linear-gradient(
    135deg,
    var(--brand-500) 0%,
    var(--brand-600) 100%
  );
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
}

.model-info {
  flex: 1;
}

.model-name {
  font-weight: 500;
  color: var(--text-primary);
}

.model-format {
  font-size: 12px;
  color: var(--text-secondary);
}
</style>
