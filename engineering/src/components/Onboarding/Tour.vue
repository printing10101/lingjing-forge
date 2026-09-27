<template>
  <Teleport to="body">
    <Transition name="tour-fade">
      <div
        v-if="visible"
        class="tour-overlay"
        @click.self="handleOverlayClick"
      >
        <!-- 高亮区域 -->
        <div
          v-if="currentStep?.target"
          class="tour-highlight"
          :style="highlightStyle"
        />

        <!-- 引导弹窗 -->
        <Transition name="tour-pop">
          <div
            v-if="currentStep"
            class="tour-popover"
            :style="popoverStyle"
            :class="{ 'tour-popover--center': !currentStep.target }"
          >
            <!-- 步骤指示器 -->
            <div class="tour-indicators">
              <span
                v-for="n in steps.length"
                :key="n"
                class="tour-indicator"
                :class="{
                  'tour-indicator--active': (n - 1) === currentStepIndex,
                  'tour-indicator--completed': (n - 1) < currentStepIndex
                }"
              />
            </div>

            <!-- 标题 -->
            <h3 class="tour-title">
              {{ currentStep.title }}
            </h3>

            <!-- 描述 -->
            <p class="tour-description">
              {{ currentStep.description }}
            </p>

            <!-- 视觉指引图片 -->
            <div
              v-if="currentStep.image"
              class="tour-image"
            >
              <img
                :src="currentStep.image"
                :alt="currentStep.title"
              >
            </div>

            <!-- 操作按钮 -->
            <div class="tour-actions">
              <el-button
                v-if="currentStepIndex > 0"
                size="small"
                @click="prev"
              >
                {{ t('onboardingTour.prev') }}
              </el-button>
              <el-button
                size="small"
                @click="skip"
              >
                {{ t('onboardingTour.skip') }}
              </el-button>
              <el-button
                v-if="currentStepIndex < steps.length - 1"
                type="primary"
                size="small"
                @click="next"
              >
                {{ t('onboardingTour.next') }}
              </el-button>
              <el-button
                v-else
                type="primary"
                size="small"
                @click="finish"
              >
                {{ t('onboardingTour.finish') }}
              </el-button>
            </div>
          </div>
        </Transition>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'

/** 引导步骤配置 */
export interface TourStep {
  /** 步骤标题 */
  title: string
  /** 步骤描述 */
  description: string
  /** 目标元素选择器（可选，如果不提供则居中显示） */
  target?: string
  /** 视觉指引图片 URL（可选） */
  image?: string
  /** 弹窗位置 */
  placement?: 'top' | 'bottom' | 'left' | 'right'
}

/** Props 定义 */
interface Props {
  /** 引导步骤配置 */
  steps: TourStep[]
  /** 是否自动开始引导 */
  autoStart?: boolean
  /** 存储键名，用于进度记忆 */
  storageKey?: string
}

const props = withDefaults(defineProps<Props>(), {
  autoStart: false,
  storageKey: 'tour_progress'
})

/** Emits 定义 */
const emit = defineEmits<{
  (e: 'start'): void
  (e: 'step-change', index: number): void
  (e: 'finish'): void
  (e: 'skip', index: number): void
}>()

const { t } = useI18n()

// 状态
const visible = ref(false)
const currentStepIndex = ref(0)
const targetRect = ref<DOMRect | null>(null)

// 计算属性
const currentStep = computed(() => props.steps[currentStepIndex.value] || null)

const highlightStyle = computed(() => {
  if (!targetRect.value) return {}
  const padding = 8
  return {
    top: `${targetRect.value.top - padding}px`,
    left: `${targetRect.value.left - padding}px`,
    width: `${targetRect.value.width + padding * 2}px`,
    height: `${targetRect.value.height + padding * 2}px`
  }
})

const popoverStyle = computed(() => {
  if (!currentStep.value?.target || !targetRect.value) {
    return {}
  }

  const placement = currentStep.value.placement || 'bottom'
  const rect = targetRect.value
  const offset = 16
  const style: Record<string, string> = {}

  switch (placement) {
    case 'top':
      style.bottom = `${window.innerHeight - rect.top + offset}px`
      style.left = `${rect.left + rect.width / 2}px`
      style.transform = 'translateX(-50%)'
      break
    case 'bottom':
      style.top = `${rect.bottom + offset}px`
      style.left = `${rect.left + rect.width / 2}px`
      style.transform = 'translateX(-50%)'
      break
    case 'left':
      style.top = `${rect.top + rect.height / 2}px`
      style.right = `${window.innerWidth - rect.left + offset}px`
      style.transform = 'translateY(-50%)'
      break
    case 'right':
      style.top = `${rect.top + rect.height / 2}px`
      style.left = `${rect.right + offset}px`
      style.transform = 'translateY(-50%)'
      break
  }

  return style
})

// 方法
function start() {
  if (props.steps.length === 0) return
  
  // 从 localStorage 恢复进度
  const savedIndex = loadProgress()
  if (savedIndex !== null && savedIndex < props.steps.length) {
    currentStepIndex.value = savedIndex
  } else {
    currentStepIndex.value = 0
  }
  
  visible.value = true
  emit('start')
  updateTargetRect()
}

function next() {
  if (currentStepIndex.value < props.steps.length - 1) {
    currentStepIndex.value++
    saveProgress()
    emit('step-change', currentStepIndex.value)
    nextTick(() => updateTargetRect())
  }
}

function prev() {
  if (currentStepIndex.value > 0) {
    currentStepIndex.value--
    saveProgress()
    emit('step-change', currentStepIndex.value)
    nextTick(() => updateTargetRect())
  }
}

function skip() {
  const skippedIndex = currentStepIndex.value
  visible.value = false
  clearProgress()
  emit('skip', skippedIndex)
}

function finish() {
  visible.value = false
  clearProgress()
  emit('finish')
}

function handleOverlayClick() {
  // 点击遮罩层不关闭，必须通过按钮操作
}

function updateTargetRect() {
  if (!currentStep.value?.target) {
    targetRect.value = null
    return
  }

  const element = document.querySelector(currentStep.value.target)
  if (element) {
    targetRect.value = element.getBoundingClientRect()
    // 滚动到目标元素
    element.scrollIntoView({ behavior: 'smooth', block: 'center' })
  } else {
    targetRect.value = null
  }
}

function saveProgress() {
  try {
    localStorage.setItem(props.storageKey, JSON.stringify({
      currentStep: currentStepIndex.value,
      timestamp: Date.now()
    }))
  } catch (e: unknown) {
    // localStorage 配额超限或被禁用时仅记录日志，不影响引导流程
    console.warn('[Tour] saveProgress: failed to write localStorage:', e)
  }
}

function loadProgress(): number | null {
  try {
    const raw = localStorage.getItem(props.storageKey)
    if (raw) {
      const data = JSON.parse(raw)
      // 只恢复 7 天内的进度
      if (Date.now() - data.timestamp < 7 * 24 * 60 * 60 * 1000) {
        return data.currentStep
      }
    }
  } catch (e: unknown) {
    // localStorage 数据损坏时清理脏数据并返回 null，避免后续每次启动都重复抛错
    console.warn('[Tour] loadProgress: failed to parse localStorage, ignoring saved progress:', e)
    try {
      localStorage.removeItem(props.storageKey)
    } catch {
      /* localStorage 不可用时无可清理，忽略 */
    }
  }
  return null
}

function clearProgress() {
  try {
    localStorage.removeItem(props.storageKey)
  } catch (e: unknown) {
    console.warn('[Tour] clearProgress: failed to remove localStorage:', e)
  }
}

// 监听步骤变化，更新目标位置
watch(currentStepIndex, () => {
  nextTick(() => updateTargetRect())
})

// 监听窗口大小变化
function handleResize() {
  if (visible.value) {
    updateTargetRect()
  }
}

// 生命周期
let tourTimer: ReturnType<typeof setTimeout> | null = null

onMounted(() => {
  window.addEventListener('resize', handleResize)

  // 如果配置为自动开始，则启动引导
  if (props.autoStart) {
    // 延迟启动，确保页面完全加载
    tourTimer = setTimeout(() => {
      start()
    }, 500)
  }
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  if (tourTimer !== null) {
    clearTimeout(tourTimer)
    tourTimer = null
  }
})

// 暴露方法
defineExpose({
  start,
  next,
  prev,
  skip,
  finish
})
</script>

<style scoped>
.tour-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background-color: var(--bg-overlay);
  z-index: 9999;
  pointer-events: auto;
}

.tour-highlight {
  position: fixed;
  background-color: var(--bg-overlay-white);
  border-radius: var(--radius-xs);
  box-shadow: 0 0 0 9999px var(--bg-overlay);
  pointer-events: none;
  z-index: 10000;
  transition: all 0.3s ease;
}

.tour-popover {
  position: fixed;
  background: white;
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg);
  padding: 24px;
  max-width: 400px;
  min-width: 320px;
  z-index: 10001;
  pointer-events: auto;
}

.tour-popover--center {
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
}

.tour-indicators {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
  justify-content: center;
}

.tour-indicator {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background-color: var(--border-light);
  transition: all 0.3s ease;
}

.tour-indicator--active {
  background-color: var(--accent-primary);
  width: 24px;
  border-radius: var(--radius-xs);
}

.tour-indicator--completed {
  background-color: var(--success);
}

.tour-title {
  margin: 0 0 12px;
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
}

.tour-description {
  margin: 0 0 16px;
  font-size: 14px;
  line-height: 1.6;
  color: var(--text-secondary);
}

.tour-image {
  margin-bottom: 16px;
  border-radius: var(--radius-xs);
  overflow: hidden;
  background-color: var(--bg-tertiary);
}

.tour-image img {
  width: 100%;
  height: auto;
  display: block;
}

.tour-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}

/* 过渡动画 */
.tour-fade-enter-active,
.tour-fade-leave-active {
  transition: opacity 0.3s ease;
}

.tour-fade-enter-from,
.tour-fade-leave-to {
  opacity: 0;
}

.tour-pop-enter-active,
.tour-pop-leave-active {
  transition: all 0.3s ease;
}

.tour-pop-enter-from {
  opacity: 0;
  transform: translate(-50%, -50%) scale(0.9);
}

.tour-pop-leave-to {
  opacity: 0;
  transform: translate(-50%, -50%) scale(0.9);
}

.tour-popover:not(.tour-popover--center) .tour-pop-enter-active,
.tour-popover:not(.tour-popover--center) .tour-pop-leave-active {
  transition: all 0.3s ease;
}

.tour-popover:not(.tour-popover--center) .tour-pop-enter-from {
  opacity: 0;
  transform: scale(0.9);
}

.tour-popover:not(.tour-popover--center) .tour-pop-leave-to {
  opacity: 0;
  transform: scale(0.9);
}

/* 响应式适配 */
@media (max-width: 768px) {
  .tour-popover {
    max-width: calc(100vw - 32px);
    min-width: auto;
    padding: 20px;
  }

  .tour-title {
    font-size: 16px;
  }

  .tour-description {
    font-size: 13px;
  }

  .tour-actions {
    flex-wrap: wrap;
  }

  .tour-actions .el-button {
    flex: 1;
    min-width: 80px;
  }
}
</style>
