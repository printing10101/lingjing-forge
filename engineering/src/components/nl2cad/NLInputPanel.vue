<template>
  <div class="nl-input-panel">
    <!-- 聊天历史区域 -->
    <div ref="chatContainerRef" class="chat-container">
      <!-- 欢迎消息 -->
      <div class="message assistant-message">
        <div class="message-avatar">
          <el-icon><ChatDotRound /></el-icon>
        </div>
        <div class="message-content">
          <div class="message-bubble">
            <p>{{ t("nlInputPanel.welcomeGreeting") }}</p>
            <p>{{ t("nlInputPanel.welcomeHint") }}</p>
          </div>
          <div class="message-time">
            {{ formatTime(now) }}
          </div>
        </div>
      </div>

      <!-- 用户和AI消息 -->
      <ChatMessage
        v-for="msg in messages"
        :key="msg.id"
        :message="msg"
        @confirm-params="handleConfirmParams"
        @edit-params="handleEditParams"
        @view-3d="handleView3D"
        @download="handleDownload"
      />

      <!-- 加载指示器 -->
      <div v-if="loading" class="message assistant-message">
        <div class="message-avatar">
          <el-icon><ChatDotRound /></el-icon>
        </div>
        <div class="message-content">
          <div class="message-bubble typing-indicator">
            <span /><span /><span />
          </div>
        </div>
      </div>
    </div>

    <!-- 输入区域 -->
    <ChatInputArea v-model="userInput" :disabled="loading" @send="handleSend" />

    <!-- 参数编辑对话框 -->
    <ParamEditDialog
      v-model:visible="showParamDialog"
      :params="editParams"
      @confirm="confirmEditedParams"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick } from "vue";
import { useI18n } from "vue-i18n";
import { ChatDotRound } from "@element-plus/icons-vue";
import {
  extractParams as apiExtractParams,
  generateModel as apiGenerateModel,
} from "@/api/nl2cad";
import type { CADParams } from "@/types/nl2cad";
import type { Message } from "@/components/nl2cad/chat/types";
import ChatMessage from "@/components/nl2cad/chat/ChatMessage.vue";
import ChatInputArea from "@/components/nl2cad/chat/ChatInputArea.vue";
import ParamEditDialog from "@/components/nl2cad/chat/ParamEditDialog.vue";

const { t } = useI18n();

const emit = defineEmits<{
  (
    e: "model-generated",
    modelPath: string,
    params: CADParams,
    script?: string,
    scriptParams?: Record<string, number>,
  ): void;
  (e: "view-3d", modelPath: string): void;
}>();

const chatContainerRef = ref<HTMLElement>();
const userInput = ref("");
const messages = ref<Message[]>([]);
const loading = ref(false);
const now = new Date();

// 消息唯一 id 生成器，用于 v-for key
let messageIdCounter = 0;
function nextMessageId(): string {
  return `msg-${++messageIdCounter}`;
}

// 参数编辑
const showParamDialog = ref(false);
const editParams = ref<CADParams>({} as CADParams);

function formatTime(date: Date): string {
  return date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function scrollToBottom() {
  nextTick(() => {
    if (chatContainerRef.value) {
      chatContainerRef.value.scrollTop = chatContainerRef.value.scrollHeight;
    }
  });
}

async function handleSend() {
  const text = userInput.value.trim();
  if (!text || loading.value) return;

  // 添加用户消息
  messages.value.push({
    id: nextMessageId(),
    role: "user",
    content: text,
    timestamp: new Date(),
  });
  userInput.value = "";
  loading.value = true;
  scrollToBottom();

  try {
    // 通过统一 http 客户端调用参数提取 API
    const data = await apiExtractParams({ description: text });
    const params = (data.params || {}) as CADParams;

    // 添加AI回复（参数卡片）
    messages.value.push({
      id: nextMessageId(),
      role: "assistant",
      content: "",
      timestamp: new Date(),
      type: "params",
      params,
    });
  } catch (error) {
    console.error("Failed to extract params:", error);
    messages.value.push({
      id: nextMessageId(),
      role: "assistant",
      content: t("nlInputPanel.errorUnderstand"),
      timestamp: new Date(),
      type: "text",
    });
  } finally {
    loading.value = false;
    scrollToBottom();
  }
}

async function handleConfirmParams(params?: CADParams) {
  if (!params) return;
  loading.value = true;
  scrollToBottom();

  try {
    // 通过统一 http 客户端调用模型生成 API
    const data = await apiGenerateModel({
      description:
        messages.value.filter((m) => m.role === "user").pop()?.content || "",
      output_format: "stl",
    });

    // 添加模型生成结果消息
    messages.value.push({
      id: nextMessageId(),
      role: "assistant",
      content: "",
      timestamp: new Date(),
      type: "model",
      modelPath: data.model_path,
      modelName: t("nlInputPanel.defaultModelName"),
      format: "stl",
    });

    // 通知父组件（携带参数化脚本与参数表，供滑杆面板免 LLM 调参）
    emit(
      "model-generated",
      data.model_path,
      (data.params || {}) as CADParams,
      data.script || "",
      data.parameters || {},
    );
  } catch (error) {
    console.error("Failed to generate model:", error);
    messages.value.push({
      id: nextMessageId(),
      role: "assistant",
      content: t("nlInputPanel.errorGenerateFailed"),
      timestamp: new Date(),
      type: "text",
    });
  } finally {
    loading.value = false;
    scrollToBottom();
  }
}

function handleEditParams(params?: CADParams) {
  if (!params) return;
  editParams.value = structuredClone(params);
  showParamDialog.value = true;
}

function confirmEditedParams(params: CADParams) {
  // 更新最后一条参数消息
  const lastParamsMsg = [...messages.value]
    .reverse()
    .find((m) => m.type === "params");
  if (lastParamsMsg) {
    lastParamsMsg.params = params;
  }
  showParamDialog.value = false;
}

function handleView3D(modelPath?: string) {
  if (!modelPath) return;
  emit("view-3d", modelPath);
}

function handleDownload(modelPath?: string) {
  if (!modelPath) return;
  // 触发下载
  const link = document.createElement("a");
  link.href = modelPath;
  link.download = modelPath.split("/").pop() || "model.stl";
  link.click();
}
</script>

<style scoped>
.nl-input-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--bg-primary);
}

.chat-container {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.message {
  display: flex;
  gap: 12px;
  max-width: 85%;
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

.message-time {
  font-size: 11px;
  color: var(--text-tertiary);
  padding: 0 4px;
}

/* 打字指示器 */
.typing-indicator {
  display: flex;
  gap: 4px;
  padding: 16px;
}

.typing-indicator span {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--text-tertiary);
  animation: typing 1.4s infinite ease-in-out;
}

.typing-indicator span:nth-child(1) {
  animation-delay: -0.32s;
}
.typing-indicator span:nth-child(2) {
  animation-delay: -0.16s;
}

@keyframes typing {
  0%,
  80%,
  100% {
    transform: scale(0.6);
    opacity: 0.5;
  }
  40% {
    transform: scale(1);
    opacity: 1;
  }
}

/* 滚动条样式 */
.chat-container::-webkit-scrollbar {
  width: 6px;
}

.chat-container::-webkit-scrollbar-track {
  background: transparent;
}

.chat-container::-webkit-scrollbar-thumb {
  background: var(--bg-400);
  border-radius: var(--radius-2xs);
}

.chat-container::-webkit-scrollbar-thumb:hover {
  background: var(--bg-500);
}
</style>
