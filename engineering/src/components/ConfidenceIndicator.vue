<template>
  <div
    class="confidence-indicator"
    @mouseenter="showTooltip = true"
    @mouseleave="showTooltip = false"
  >
    <div class="confidence-bar-container">
      <div
        class="confidence-bar-fill"
        :style="{
          width: `${confidence * 100}%`,
          backgroundColor: getConfidenceColor(confidence),
        }"
      />
      <div class="confidence-bar-label">
        {{ (confidence * 100).toFixed(0) }}%
      </div>
    </div>

    <div
      v-if="showTooltip && showTooltipOnHover"
      class="confidence-tooltip"
    >
      <div class="tooltip-header">
        <span class="tooltip-title">{{ $t('confidence.tooltipTitle') }}</span>
        <span
          class="tooltip-value"
          :style="{ color: getConfidenceColor(confidence) }"
        >
          {{ (confidence * 100).toFixed(2) }}%
        </span>
      </div>
      <div class="tooltip-body">
        <p><strong>{{ $t('confidence.labelLevel') }}</strong> {{ getConfidenceLabel(confidence) }}</p>
        <p><strong>{{ $t('confidence.labelDescription') }}</strong> {{ confidenceDescription }}</p>
        <div
          v-if="recommendation"
          class="tooltip-recommendation"
        >
          <strong>{{ $t('confidence.labelRecommendation') }}</strong> {{ recommendation }}
        </div>
      </div>
    </div>

    <div
      v-if="showLabel"
      class="confidence-text"
      :style="{ color: getConfidenceColor(confidence) }"
    >
      {{ getConfidenceLabel(confidence) }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { getConfidenceColor, getConfidenceLabel } from '@/utils/statusHelpers'

interface Props {
  confidence: number
  showLabel?: boolean
  showTooltipOnHover?: boolean
  showNumericValue?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  showLabel: true,
  showTooltipOnHover: true,
  showNumericValue: true,
})

const { t } = useI18n()

const showTooltip = ref(false)

const confidenceDescription = computed(() => {
  if (props.confidence >= 0.8) {
    return t('confidence.descHigh')
  }
  if (props.confidence >= 0.5) {
    return t('confidence.descMedium')
  }
  return t('confidence.descLow')
})

const recommendation = computed(() => {
  if (props.confidence >= 0.8) {
    return t('confidence.recHigh')
  }
  if (props.confidence >= 0.5) {
    return t('confidence.recMedium')
  }
  return t('confidence.recLow')
})
</script>

<style scoped>
.confidence-indicator {
  position: relative;
  display: inline-block;
  width: 100%;
}

.confidence-bar-container {
  position: relative;
  height: 24px;
  background: var(--border-medium);
  border-radius: var(--radius-lg);
  overflow: hidden;
  box-shadow: inset 0 1px 3px rgba(0, 0, 0, 0.1);
}

.confidence-bar-fill {
  height: 100%;
  transition: width 0.5s ease, background-color 0.3s ease;
  border-radius: var(--radius-lg);
}

.confidence-bar-label {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  font-size: 12px;
  font-weight: 600;
  color: var(--bg-card);
  text-shadow: var(--shadow-text);
  z-index: 1;
}

.confidence-tooltip {
  position: absolute;
  bottom: calc(100% + 8px);
  left: 50%;
  transform: translateX(-50%);
  background: var(--bg-card);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
  padding: 12px;
  min-width: 280px;
  box-shadow: var(--shadow-md);
  z-index: 1000;
  animation: fadeIn 0.2s ease;
}

@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateX(-50%) translateY(4px);
  }
  to {
    opacity: 1;
    transform: translateX(-50%) translateY(0);
  }
}

.tooltip-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border-light);
}

.tooltip-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.tooltip-value {
  font-size: 16px;
  font-weight: 700;
}

.tooltip-body {
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.6;
}

.tooltip-body p {
  margin: 4px 0;
}

.tooltip-recommendation {
  margin-top: 8px;
  padding: 8px;
  background: var(--bg-tertiary);
  border-radius: var(--radius-xs);
  font-size: 12px;
  color: var(--accent-primary);
}

.confidence-text {
  margin-top: 4px;
  font-size: 12px;
  font-weight: 500;
  text-align: center;
}
</style>
