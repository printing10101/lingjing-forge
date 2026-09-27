<template>
  <div class="ai-solo-chat">
    <!-- 聊天对话框区域 -->
    <div class="chat-messages" ref="chatContainer">
      <div 
        v-for="(msg, index) in messages" 
        :key="index"
        class="message"
        :class="msg.role"
      >
        <div class="message-header">
          <span class="avatar" :class="msg.role">
            <i v-if="msg.role === 'user'" class="fas fa-user"></i>
            <i v-else class="fas fa-robot"></i>
          </span>
          <span class="role-label">{{ msg.role === 'user' ? '你' : 'AI 助手' }}</span>
        </div>
        
        <div class="message-content">
          <template v-if="msg.type === 'text'">
            <div class="text-content">{{ msg.content }}</div>
            
            <!-- 代码块显示 -->
            <template v-if="msg.hasCode">
              <div class="code-block-wrapper">
                <div class="code-block-header">
                  <span class="filename">{{ msg.filename || '代码' }}</span>
                  <div class="code-actions">
                    <button 
                      v-if="!msg.isApplied"
                      class="btn-apply" 
                      @click="$emit('apply-changes', msg.code)"
                    >
                      <i class="fas fa-check"></i> 应用
                    </button>
                    <button class="btn-copy" @click="handleCopy(msg.code)">
                      <i class="fas fa-copy"></i> 复制
                    </button>
                  </div>
                </div>
                <pre class="code-block"><code>{{ msg.code }}</code></pre>
                
                <!-- 应用状态 -->
                <div v-if="msg.isApplied" class="applied-badge">
                  <i class="fas fa-check-circle"></i> 已应用
                </div>
              </div>
            </template>
          </template>
          
          <!-- Loading 状态 -->
          <template v-else-if="msg.type === 'loading'">
            <div class="loading-indicator">
              <span class="typing-dot"></span>
              <span class="typing-dot"></span>
              <span class="typing-dot"></span>
            </div>
          </template>
          
          <!-- 操作提示 -->
          <template v-else-if="msg.type === 'suggestion'">
            <div class="suggestion-box">
              <i class="fas fa-lightbulb"></i>
              <span>{{ msg.content }}</span>
              <button 
                v-if="msg.canStage"
                class="btn-git-stage" 
                @click="$emit('suggest-git-stage', msg.files)"
              >
                <i class="fas fa-git-commit"></i> 暂存更改
              </button>
            </div>
          </template>
        </div>
      </div>
    </div>
    
    <!-- 输入区域 -->
    <div class="chat-input">
      <div class="input-toolbar">
        <button 
          v-for="shortcut in suggestedShortcuts" 
          :key="shortcut.label"
          class="btn-shortcut"
          @click="insertShortcut(shortcut)"
          :title="shortcut.desc"
        >
          <i :class="shortcut.icon"></i>
          {{ shortcut.label }}
        </button>
      </div>
      
      <div class="input-box">
        <textarea
          v-model="inputText"
          placeholder="描述你想让 AI 帮你做什么..."
          @keydown.enter.exact.prevent="handleSend"
          rows="1"
          ref="inputTextarea"
        ></textarea>
        <button 
          class="btn-send" 
          @click="handleSend"
          :disabled="!inputText.trim() || isGenerating"
        >
          <i class="fas fa-paper-plane"></i>
        </button>
      </div>
      
      <!-- 生成进度 -->
      <div v-if="isGenerating" class="generation-progress">
        <div class="progress-bar">
          <div class="progress-fill" :style="{ width: generationProgress + '%' }"></div>
        </div>
        <span class="progress-text">{{ generationStatus }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, nextTick, watch } from 'vue';

interface Message {
  role: 'user' | 'assistant';
  type: 'text' | 'loading' | 'suggestion';
  content: string;
  filename?: string;
  code?: string;
  hasCode: boolean;
  isApplied?: boolean;
  canStage?: boolean;
  files?: string[];
}

interface Shortcut {
  label: string;
  icon: string;
  desc: string;
  template: string;
}

const props = defineProps<{
  modelValue?: string;
}>();

const emit = defineEmits<{
  (e: 'change-generation', code: string, filename: string): void;
  (e: 'apply-changes', code: string): void;
  (e: 'suggest-git-stage', files: string[]): void;
}>();

const chatContainer = ref<HTMLDivElement>();
const inputTextarea = ref<HTMLTextAreaElement>();

const messages = ref<Message[]>([]);
const inputText = ref('');
const isGenerating = ref(false);
const generationProgress = ref(0);
const generationStatus = ref('');

// 快捷提示
const suggestedShortcuts = ref<Shortcut[]>([
  {
    label: '修改样式',
    icon: 'fas fa-paint-brush',
    desc: '修改当前文件的样式',
    template: '请帮我修改当前文件的样式，保持简洁现代的设计风格，主色调使用蓝色 (#0e639c)，注意响应式布局和用户体验。'
  },
  {
    label: '优化布局',
    icon: 'fas fa-th-large',
    desc: '优化页面布局结构',
    template: '请帮我优化当前页面的布局，提升用户操作流畅度，确保在不同屏幕尺寸下都有良好的展示效果。'
  },
  {
    label: '添加交互',
    icon: 'fas fa-mouse-pointer',
    desc: '添加用户交互体验',
    template: '请为当前页面添加合理的用户交互，包括加载状态、错误提示、成功反馈等，提升用户体验。'
  }
]);

const currentFilename = computed(() => {
  // TODO: 从当前文件状态获取
  return 'current.ts';
});

/**
 * 发送消息
 */
const handleSend = async () => {
  if (!inputText.value.trim() || isGenerating.value) return;
  
  const userMsg: Message = {
    role: 'user',
    type: 'text',
    content: inputText.value
  };
  
  messages.value.push(userMsg);
  inputText.value = '';
  isGenerating.value = true;
  generationProgress.value = 0;
  generationStatus.value = '正在思考...';
  
  // 添加 AI 加载消息
  messages.value.push({
    role: 'assistant',
    type: 'loading',
    content: ''
  });
  
  await nextTick();
  scrollToBottom();
  
  // 模拟 AI 生成（实际应调用后端 LLM API）
  await simulateAIResponse(userMsg.content);
};

/**
 * 模拟 AI 响应
 */
const simulateAIResponse = async (userQuery: string) => {
  const loadingIndex = messages.value.length - 1;
  
  // 分阶段更新加载状态
  const progressSteps = [
    { progress: 10, status: '理解需求中...' },
    { progress: 30, status: '分析代码结构...' },
    { progress: 50, status: '生成修改方案...' },
    { progress: 70, status: '优化代码质量...' },
    { progress: 90, status: '准备预览...' },
    { progress: 100, status: '完成' }
  ];
  
  for (const step of progressSteps) {
    await new Promise(resolve => setTimeout(resolve, 300));
    generationProgress.value = step.progress;
    generationStatus.value = step.status;
  }
  
  // 替换加载消息为生成结果
  const hasCode = userQuery.toLowerCase().includes('代码') || userQuery.toLowerCase().includes('修改');
  
  messages.value[loadingIndex] = {
    role: 'assistant',
    type: 'text',
    content: getDefaultResponse(userQuery),
    hasCode,
    filename: currentFilename.value,
    code: generateSampleCode(userQuery),
    isApplied: false,
    canStage: true,
    files: [currentFilename.value]
  };
  
  isGenerating.value = false;
  scrollToBottom();
};

/**
 * 获取默认响应
 */
const getDefaultResponse = (query: string): string => {
  if (query.includes('样式')) {
    return '我将为您优化页面样式，保持简洁现代的设计风格，主色调使用 Trae 风格的蓝色 (#0e639c)。\n\n请先确认您希望修改的具体页面或组件。';
  }
  
  if (query.includes('布局') || query.includes('结构')) {
    return '我将为您优化页面布局结构，确保良好的用户体验和响应式设计。\n\n请问您希望针对哪个具体模块进行布局优化？';
  }
  
  if (query.includes('交互')) {
    return '我将为页面添加合理的用户交互体验，包括加载状态、错误提示和成功反馈。\n\n请指明需要添加交互的具体功能点。';
  }
  
  return '请问您希望我如何帮助您？您可以让我：\n• 修改样式\n• 优化布局\n• 添加交互\n• 生成代码';
};

/**
 * 生成示例代码
 */
const generateSampleCode = (query: string): string => {
  if (query.includes('样式')) {
    return `/* 优化后的样式 */
.container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 20px;
  background: #1e1e1e;
  color: #d4d4d4;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 24px;
  background: #252526;
  border-radius: 8px;
  margin-bottom: 24px;
}

.primary-btn {
  background: #0e639c;
  color: white;
  border: none;
  padding: 10px 24px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.3s ease;
}

.primary-btn:hover {
  background: #1177bb;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(14, 99, 156, 0.3);
}

@media (max-width: 768px) {
  .container {
    padding: 12px;
  }
  
  .header {
    flex-direction: column;
    gap: 12px;
  }
}`;
  }
  
  if (query.includes('布局')) {
    return `.main-layout {
  display: grid;
  grid-template-columns: 1fr 250px;
  gap: 20px;
  min-height: calc(100vh - 64px);
}

.sidebar {
  width: 250px;
  min-width: 200px;
  background: #252526;
  border-right: 1px solid #333;
  padding: 16px;
}

.editor-area {
  flex: 1;
  overflow: hidden;
  background: #1e1e1e;
  border-radius: 8px;
}

.ai-panel {
  width: 400px;
  min-width: 350px;
  background: #252526;
  border-left: 1px solid #333;
  display: flex;
  flex-direction: column;
}

@media (max-width: 1200px) {
  .main-layout {
    grid-template-columns: 1fr;
  }
  
  .sidebar, .ai-panel {
    display: none;
  }
}`;
  }
  
  // 默认代码模板
  return `.optimized-component {
  /* 添加响应式设计和现代化样式 */
}`;
};

/**
 * 追加代码生成结果
 */
const appendCodeResult = (code: string, filename: string) => {
  emit('change-generation', code, filename);
};

/**
 * 滚动到底部
 */
const scrollToBottom = () => {
  nextTick(() => {
    chatContainer.value?.scrollTo({
      top: chatContainer.value.scrollHeight,
      behavior: 'smooth'
    });
  });
};

/**
 * 插入快捷提示
 */
const insertShortcut = (shortcut: Shortcut) => {
  inputText.value = shortcut.template;
  inputTextarea.value?.focus();
};

/**
 * 复制代码
 */
const handleCopy = (code: string) => {
  navigator.clipboard.writeText(code);
};

/**
 * 监听输入框自动调整高度
 */
watch(
  inputText,
  () => {
    if (inputTextarea.value) {
      inputTextarea.value.style.height = 'auto';
      inputTextarea.value.style.height = Math.min(inputTextarea.value.scrollHeight, 150) + 'px';
    }
  }
);

// 初始化空消息
messages.value.push({
  role: 'assistant',
  type: 'text',
  content: '你好！我是您的前端设计助手，可以帮您：\n• 优化页面样式和布局\n• 生成高质量的代码\n• 添加交互体验\n\n请描述您的需求。',
  hasCode: false
});
</script>

<style scoped lang="scss">
.ai-solo-chat {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #252526;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.message {
  max-width: 85%;
  
  &.user {
    align-self: flex-end;
  }
  
  &.assistant {
    align-self: flex-start;
  }
}

.message-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  
  .avatar {
    width: 32px;
    height: 32px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 14px;
    
    &.user {
      background: #0e639c;
      color: white;
    }
    
    &.assistant {
      background: #4ec9b0;
      color: white;
    }
  }
  
  .role-label {
    font-size: 12px;
    color: #888;
  }
}

.message-content {
  background: #2c2c2e;
  border-radius: 8px;
  padding: 12px 16px;
  
  .text-content {
    white-space: pre-wrap;
    line-height: 1.6;
    color: #d4d4d4;
    font-size: 14px;
  }
}

.code-block-wrapper {
  margin-top: 12px;
  border: 1px solid #333;
  border-radius: 8px;
  overflow: hidden;
  
  .code-block-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    background: #1e1e1e;
    padding: 8px 12px;
    font-size: 12px;
    color: #888;
    border-bottom: 1px solid #333;
    
    .filename {
      font-family: 'Fira Code', monospace;
    }
    
    .code-actions {
      display: flex;
      gap: 8px;
      
      button {
        background: #2c2c2e;
        border: 1px solid #333;
        color: #d4d4d4;
        padding: 4px 8px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 12px;
        transition: all 0.2s ease;
        
        &:hover {
          background: #0e639c;
          border-color: #0e639c;
        }
      }
    }
  }
  
  .code-block {
    margin: 0;
    padding: 12px;
    overflow-x: auto;
    font-family: 'Fira Code', monospace;
    font-size: 13px;
    line-height: 1.5;
    color: #d4d4d4;
    background: #1e1e1e;
    
    code {
      font-family: inherit;
    }
  }
  
  .applied-badge {
    background: #2d5a2d;
    color: #90ee90;
    padding: 4px 12px;
    font-size: 12px;
    display: flex;
    align-items: center;
    gap: 4px;
    border-top: 1px solid #333;
  }
}

.btn-apply {
  background: #2d5a2d;
  border: none;
  color: #90ee90;
  padding: 4px 12px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
  transition: all 0.2s ease;
  
  &:hover {
    background: #3d7a3d;
  }
}

.btn-git-stage {
  display: block;
  margin-top: 8px;
  width: 100%;
  background: #4a6da7;
  border: none;
  color: white;
  padding: 6px 12px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 13px;
  transition: all 0.2s ease;
  
  &:hover {
    background: #5a7db7;
  }
}

.loading-indicator {
  display: flex;
  gap: 4px;
  align-items: center;
  
  .typing-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #888;
    animation: typing 1.4s infinite;
    
    &:nth-child(2) { animation-delay: 0.2s; }
    &:nth-child(3) { animation-delay: 0.4s; }
  }
}

@keyframes typing {
  0%, 60%, 100% { transform: translateY(0); }
  30% { transform: translateY(-8px); }
}

.suggestion-box {
  background: #3d4c3d;
  border: 1px solid #4a6da7;
  border-radius: 6px;
  padding: 12px;
  color: #90ee90;
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.chat-input {
  border-top: 1px solid #333;
  padding: 12px;
}

.input-toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}

.btn-shortcut {
  background: #2c2c2e;
  border: 1px solid #333;
  color: #888;
  padding: 4px 10px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
  transition: all 0.2s ease;
  
  i {
    margin-right: 4px;
  }
  
  &:hover {
    background: #0e639c;
    border-color: #0e639c;
    color: white;
  }
}

.input-box {
  position: relative;
}

textarea {
  width: 100%;
  background: #1e1e1e;
  border: 1px solid #333;
  border-radius: 6px;
  padding: 10px 40px 10px 12px;
  color: #d4d4d4;
  font-size: 14px;
  resize: none;
  font-family: inherit;
  outline: none;
  transition: border-color 0.2s ease;
  
  &:focus {
    border-color: #0e639c;
  }
  
  &::placeholder {
    color: #666;
  }
}

.btn-send {
  position: absolute;
  right: 8px;
  bottom: 8px;
  width: 32px;
  height: 32px;
  background: #0e639c;
  border: none;
  border-radius: 6px;
  color: white;
  cursor: pointer;
  transition: all 0.2s ease;
  
  &:hover:not(:disabled) {
    background: #1177bb;
    transform: scale(1.05);
  }
  
  &:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
}

.generation-progress {
  margin-top: 8px;
}

.progress-bar {
  height: 4px;
  background: #1e1e1e;
  border-radius: 2px;
  overflow: hidden;
  
  .progress-fill {
    height: 100%;
    background: linear-gradient(90deg, #0e639c, #4ec9b0);
    transition: width 0.3s ease;
  }
}

.progress-text {
  font-size: 12px;
  color: #888;
  margin-top: 4px;
}
</style>
