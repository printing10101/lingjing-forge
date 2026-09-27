<template>
  <Transition
    name="splash-fade"
    @after-leave="$emit('complete')"
  >
    <div
      v-if="visible"
      class="splash-overlay"
    >
      <!-- 背景粒子效果 -->
      <div class="particles">
        <!-- 静态列表，index 作为 key 可接受 -->
        <div
          v-for="i in 20"
          :key="i"
          class="particle"
          :style="particleStyle(i)"
        />
      </div>

      <!-- 主内容区域 -->
      <div class="splash-content">
        <!-- 3D 线框立方体动画 -->
        <div class="cube-container">
          <svg
            viewBox="0 0 200 200"
            class="cube-svg"
          >
            <!-- 外圈光晕 -->
            <circle
              cx="100"
              cy="100"
              r="90"
              class="glow-ring"
            />
            <circle
              cx="100"
              cy="100"
              r="75"
              class="glow-ring inner"
            />

            <!-- 3D 立方体线框 -->
            <g
              class="cube-wireframe"
              transform="translate(100, 95)"
            >
              <!-- 后面 -->
              <polygon
                :points="backFace"
                class="cube-face back"
              />
              <!-- 前面 -->
              <polygon
                :points="frontFace"
                class="cube-face front"
              />
              <!-- 连接线 -->
              <line
                v-for="(line, i) in connectingLines"
                :key="'l'+i"
                :x1="line[0]"
                :y1="line[1]"
                :x2="line[2]"
                :y2="line[3]"
                class="cube-edge"
              />
              <!-- 顶点 -->
              <circle
                v-for="(v, i) in allVertices"
                :key="'v'+i"
                :cx="v[0]"
                :cy="v[1]"
                r="3"
                class="cube-vertex"
                :style="{ animationDelay: `${i * 0.1}s` }"
              />
            </g>
          </svg>
        </div>

        <!-- 应用名称 -->
        <div class="app-brand">
          <h1 class="app-name">
            <!-- 静态列表，index 作为 key 可接受 -->
            <span
              v-for="(char, i) in appNameChars"
              :key="i"
              class="char"
              :style="{ animationDelay: `${0.8 + i * 0.1}s` }"
            >
              {{ char }}
            </span>
          </h1>
          <p class="app-subtitle">
            {{ t('splashScreen.appSubtitle') }}
          </p>
          <p class="app-version-text">
            V4 · v{{ version }}
          </p>
        </div>

        <!-- 进度条 -->
        <div class="progress-container">
          <div class="progress-track">
            <div
              class="progress-fill"
              :style="{ width: `${progress}%` }"
            />
          </div>
          <p class="progress-text">
            {{ statusText }}
          </p>
        </div>
      </div>

      <!-- 底部信息 -->
      <div class="splash-footer">
        <span>Copyright &copy; 2026 Lingjing Manufacturing</span>
      </div>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useVersionStore } from '@/stores/version'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

const visible = ref(true)
const progress = ref(0)
const statusText = ref(t('splashScreen.statusInit'))

const versionStore = useVersionStore()
const version = computed(() => versionStore.frontendVersion || '2.5.0')

const appNameChars = t('splashScreen.appName').split('')

// 等轴测投影参数
const size = 45
const angle = Math.PI / 6

const frontFace = computed(() => {
  const cos = Math.cos(angle)
  const sin = Math.sin(angle)
  const s = size
  return `0,${-s} ${s * cos},${-s + s * sin} 0,0 ${-s * cos},${-s + s * sin}`
})

const backFace = computed(() => {
  const cos = Math.cos(angle)
  const sin = Math.sin(angle)
  const s = size
  const ox = 30
  const oy = -18
  return `${ox},${-s + oy} ${ox + s * cos},${-s + s * sin + oy} ${ox},${s * 0 + oy} ${ox - s * cos},${-s + s * sin + oy}`
})

const allVertices = computed(() => {
  const cos = Math.cos(angle)
  const sin = Math.sin(angle)
  const s = size
  const ox = 30
  const oy = -18
  return [
    [0, -s], [s * cos, -s + s * sin], [0, 0], [-s * cos, -s + s * sin],
    [ox, -s + oy], [ox + s * cos, -s + s * sin + oy], [ox, oy], [ox - s * cos, -s + s * sin + oy]
  ]
})

const connectingLines = computed(() => {
  const cos = Math.cos(angle)
  const sin = Math.sin(angle)
  const s = size
  const ox = 30
  const oy = -18
  return [
    [0, -s, ox, -s + oy],
    [s * cos, -s + s * sin, ox + s * cos, -s + s * sin + oy],
    [0, 0, ox, oy],
    [-s * cos, -s + s * sin, ox - s * cos, -s + s * sin + oy]
  ]
})

function particleStyle(_i: number) {
  const x = Math.random() * 100
  const y = Math.random() * 100
  const delay = Math.random() * 5
  const duration = 3 + Math.random() * 4
  const size = 2 + Math.random() * 4
  return {
    left: `${x}%`,
    top: `${y}%`,
    width: `${size}px`,
    height: `${size}px`,
    animationDelay: `${delay}s`,
    animationDuration: `${duration}s`
  }
}

const statusMessages = computed(() => [
  { at: 0, text: t('splashScreen.statusInit') },
  { at: 15, text: t('splashScreen.statusLoadingCore') },
  { at: 35, text: t('splashScreen.statusStartingBackend') },
  { at: 55, text: t('splashScreen.statusInit3dEngine') },
  { at: 75, text: t('splashScreen.statusLoadingConfig') },
  { at: 90, text: t('splashScreen.statusReady') }
])

// 定时器ID，用于组件卸载时清理
let intervalId: ReturnType<typeof setInterval> | null = null
let timeoutId: ReturnType<typeof setTimeout> | null = null

onMounted(() => {
  // 模拟加载进度（实际项目中应监听真实加载事件）
  intervalId = setInterval(() => {
    if (progress.value < 100) {
      progress.value += Math.random() * 3 + 1
      if (progress.value > 100) progress.value = 100

      // 更新状态文本
      for (let i = statusMessages.value.length - 1; i >= 0; i--) {
        if (progress.value >= statusMessages.value[i].at) {
          statusText.value = statusMessages.value[i].text
          break
        }
      }
    } else {
      if (intervalId) clearInterval(intervalId)
      // 完成后延迟隐藏
      timeoutId = setTimeout(() => {
        visible.value = false
      }, 500)
    }
  }, 80)
})

onUnmounted(() => {
  // 组件卸载时清理定时器，防止内存泄漏
  if (intervalId) clearInterval(intervalId)
  if (timeoutId) clearTimeout(timeoutId)
})
</script>

<style scoped>
.splash-overlay {
  position: fixed;
  inset: 0;
  z-index: 9999;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: var(--splash-bg-gradient);
  overflow: hidden;
}

/* 粒子效果 */
.particles {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.particle {
  position: absolute;
  background: var(--splash-glow-soft);
  border-radius: 50%;
  animation: float-particle linear infinite;
  opacity: 0;
}

@keyframes float-particle {
  0% {
    opacity: 0;
    transform: translateY(0) scale(0);
  }
  20% {
    opacity: 1;
    transform: translateY(-20px) scale(1);
  }
  80% {
    opacity: 0.5;
  }
  100% {
    opacity: 0;
    transform: translateY(-100px) scale(0.5);
  }
}

/* 主内容 */
.splash-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 32px;
  z-index: 1;
}

/* 立方体容器 */
.cube-container {
  width: 180px;
  height: 180px;
  animation: cube-entrance 1s ease-out;
}

@keyframes cube-entrance {
  from {
    opacity: 0;
    transform: scale(0.5) rotate(-10deg);
  }
  to {
    opacity: 1;
    transform: scale(1) rotate(0deg);
  }
}

.cube-svg {
  width: 100%;
  height: 100%;
  animation: cube-rotate 8s linear infinite;
}

@keyframes cube-rotate {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.glow-ring {
  fill: none;
  stroke: var(--splash-stroke-faint);
  stroke-width: 1;
  animation: pulse-ring 2s ease-in-out infinite;
}

.glow-ring.inner {
  stroke: var(--splash-stroke-light);
  animation-delay: 0.5s;
}

@keyframes pulse-ring {
  0%, 100% {
    opacity: 0.3;
    transform: scale(1);
  }
  50% {
    opacity: 0.8;
    transform: scale(1.05);
  }
}

.cube-face {
  fill: none;
  stroke-width: 2;
}

.cube-face.back {
  stroke: var(--splash-stroke-medium);
}

.cube-face.front {
  stroke: var(--splash-stroke-bright);
  fill: var(--splash-fill-faint);
}

.cube-edge {
  stroke: var(--splash-stroke-strong);
  stroke-width: 1.5;
}

.cube-vertex {
  fill: var(--text-white);
  animation: vertex-pulse 1.5s ease-in-out infinite;
}

@keyframes vertex-pulse {
  0%, 100% {
    r: 3;
    opacity: 0.8;
  }
  50% {
    r: 5;
    opacity: 1;
  }
}

/* 品牌文字 */
.app-brand {
  text-align: center;
}

.app-name {
  font-size: 2.5rem;
  font-weight: 700;
  margin: 0;
  letter-spacing: 0.2em;
  background: var(--splash-accent-gradient);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.app-name .char {
  display: inline-block;
  opacity: 0;
  animation: char-appear 0.5s ease-out forwards;
}

@keyframes char-appear {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.app-subtitle {
  margin: 12px 0 8px;
  font-size: 1rem;
  color: var(--text-white-60);
  letter-spacing: 0.1em;
  animation: fade-in 1s ease-out 1.5s both;
}

.app-version-text {
  margin: 0;
  font-size: 0.85rem;
  color: var(--text-white-40);
  animation: fade-in 1s ease-out 1.8s both;
}

@keyframes fade-in {
  from { opacity: 0; }
  to { opacity: 1; }
}

/* 进度条 */
.progress-container {
  width: 280px;
  text-align: center;
  animation: fade-in 0.5s ease-out 0.5s both;
}

.progress-track {
  height: 3px;
  background: var(--splash-white-10);
  border-radius: var(--radius-3xs);
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: var(--splash-progress-gradient);
  border-radius: var(--radius-3xs);
  transition: width 0.3s ease-out;
  box-shadow: 0 0 10px var(--splash-glow);
}

.progress-text {
  margin: 12px 0 0;
  font-size: 0.8rem;
  color: var(--text-white-50);
}

/* 底部 */
.splash-footer {
  position: absolute;
  bottom: 24px;
  font-size: 0.75rem;
  color: var(--text-white-30);
}

/* 过渡动画 */
.splash-fade-leave-active {
  transition: opacity 0.6s ease-out;
}

.splash-fade-leave-to {
  opacity: 0;
}
</style>
