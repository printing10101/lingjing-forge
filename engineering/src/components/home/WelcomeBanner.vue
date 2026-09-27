<template>
  <section class="welcome-banner">
    <div class="banner-content">
      <div class="banner-text">
        <h2>{{ greeting }}{{ t('home.greetingOperator') }}</h2>
        <p class="banner-date">
          {{ currentDateTime }}
        </p>
      </div>
    </div>
    <div class="banner-illustration">
      <svg
        viewBox="0 0 120 120"
        class="banner-svg"
      >
        <circle
          cx="100"
          cy="20"
          r="40"
          fill="rgba(255,255,255,0.06)"
        />
        <circle
          cx="105"
          cy="95"
          r="28"
          fill="rgba(255,255,255,0.04)"
        />
        <rect
          x="20"
          y="45"
          width="80"
          height="50"
          rx="8"
          fill="rgba(255,255,255,0.08)"
          stroke="rgba(255,255,255,0.12)"
          stroke-width="1"
        />
        <rect
          x="30"
          y="35"
          width="22"
          height="16"
          rx="4"
          fill="rgba(255,255,255,0.1)"
        />
        <rect
          x="58"
          y="28"
          width="22"
          height="23"
          rx="4"
          fill="rgba(255,255,255,0.14)"
        />
        <rect
          x="30"
          y="68"
          width="60"
          height="4"
          rx="2"
          fill="rgba(255,255,255,0.08)"
        />
        <rect
          x="30"
          y="78"
          width="45"
          height="4"
          rx="2"
          fill="rgba(255,255,255,0.08)"
        />
        <rect
          x="30"
          y="88"
          width="55"
          height="4"
          rx="2"
          fill="rgba(255,255,255,0.06)"
        />
        <circle
          cx="95"
          cy="25"
          r="6"
          fill="none"
          stroke="rgba(255,255,255,0.25)"
          stroke-width="1.5"
        />
        <circle
          cx="95"
          cy="25"
          r="2.5"
          fill="rgba(255,255,255,0.2)"
        />
      </svg>
    </div>
  </section>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

defineProps<{
  greeting: string
  currentDateTime: string
}>()

const { t } = useI18n()
</script>

<style scoped>
.welcome-banner {
  position: relative;
  background: linear-gradient(135deg, var(--brand-600) 0%, var(--brand-500) 50%, var(--brand-400) 100%);
  border-radius: var(--radius-xl);
  padding: 28px 36px;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: space-between;
  box-shadow: 0 4px 16px -4px rgba(0, 122, 255, 0.25), var(--shadow-md);
}

.welcome-banner::before {
  content: '';
  position: absolute;
  top: -50%;
  right: -10%;
  width: 300px;
  height: 300px;
  background: radial-gradient(circle, rgba(255, 255, 255, 0.08) 0%, transparent 70%);
  pointer-events: none;
}

.banner-content {
  position: relative;
  z-index: 1;
}

.banner-text h2 {
  margin: 0 0 6px;
  font-size: 1.4rem;
  font-weight: 700;
  color: var(--text-white);
  letter-spacing: -0.01em;
  line-height: 1.3;
}

.banner-date {
  margin: 0;
  font-size: 0.825rem;
  color: var(--text-white-75);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.01em;
}

.banner-illustration {
  position: relative;
  z-index: 1;
  flex-shrink: 0;
  pointer-events: none;
}

.banner-svg {
  width: 120px;
  height: 120px;
}

@media (max-width: 900px) {
  .banner-illustration {
    display: none;
  }
}
</style>