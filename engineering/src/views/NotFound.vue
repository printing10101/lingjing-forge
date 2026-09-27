<template>
  <div class="not-found-page">
    <el-card
      class="not-found-card"
      shadow="never"
    >
      <div class="not-found-content">
        <div class="not-found-icon">
          <svg
            width="220"
            height="220"
            viewBox="0 0 220 220"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
          >
            <circle
              cx="110"
              cy="110"
              r="100"
              stroke="var(--border-light)"
              stroke-width="3"
              fill="var(--bg-tertiary)"
            />
            <text
              x="110"
              y="125"
              text-anchor="middle"
              font-size="72"
              font-weight="700"
              fill="var(--accent-primary)"
              font-family="Arial, sans-serif"
            >404</text>
            <line
              x1="70"
              y1="150"
              x2="150"
              y2="150"
              stroke="var(--border-light)"
              stroke-width="2"
              stroke-linecap="round"
            />
            <circle
              cx="85"
              cy="80"
              r="6"
              fill="var(--text-tertiary)"
            />
            <circle
              cx="110"
              cy="75"
              r="6"
              fill="var(--text-tertiary)"
            />
            <circle
              cx="135"
              cy="80"
              r="6"
              fill="var(--text-tertiary)"
            />
            <path
              d="M80 170 Q110 185 140 170"
              stroke="var(--border-light)"
              stroke-width="2.5"
              fill="none"
              stroke-linecap="round"
            />
          </svg>
        </div>
        <h2 class="not-found-title">
          {{ $t('notFound.title') }}
        </h2>
        <p class="not-found-desc">
          {{ $t('notFound.desc') }}
        </p>
        <el-button
          type="primary"
          size="large"
          @click="goHome"
        >
          {{ $t('notFound.backHome') }}
        </el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()

const goHome = (): void => {
  router.push('/')
}

onMounted(() => {
  // 404 错误日志：访问不存在路径时不输出控制台日志
})
</script>

<style scoped>
.not-found-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 80vh;
  padding: 20px;
}

.not-found-card {
  width: 100%;
  max-width: 520px;
  border: none;
  background: transparent;
}

.not-found-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  padding: 20px 0;
}

.not-found-icon {
  margin-bottom: 24px;
}

.not-found-title {
  font-size: 28px;
  font-weight: 700;
  color: var(--text-primary);
  margin: 0 0 12px;
}

.not-found-desc {
  font-size: 15px;
  color: var(--text-tertiary);
  margin: 0 0 32px;
  line-height: 1.6;
}
</style>
