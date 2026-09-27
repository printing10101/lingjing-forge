<template>
  <div class="content-card">
    <div class="content-card__header">
      <span class="content-card__title">
        <el-icon style="margin-right: 6px;"><MagicStick /></el-icon>
        {{ $t('settings.aiSovereignty') }}
      </span>
      <el-tag
        type="success"
        size="small"
      >
        {{ $t('settings.sovereigntyMode') }}
      </el-tag>
    </div>
    <div class="content-card__body">
      <el-alert
        v-if="showSovereigntyIntro"
        :title="$t('settings.autonomyModeTitle')"
        type="info"
        :closable="true"
        show-icon
        style="margin-bottom: 20px;"
        @close="emit('update:showSovereigntyIntro', false)"
      >
        <div>
          <p><strong>{{ $t('settings.aiAutonomyLevel') }}</strong>{{ $t('settings.autonomyModeDesc') }}</p>
          <ul>
            <li><strong>0 - {{ $t('settings.fullyManual') }}</strong>：{{ $t('settings.autonomyLevel0') }}</li>
            <li><strong>1 - {{ $t('settings.confirmRequired') }}</strong>：{{ $t('settings.autonomyLevel1') }}</li>
            <li><strong>2 - {{ $t('settings.recommended') }}</strong>：{{ $t('settings.autonomyLevel2') }}</li>
            <li><strong>3 - {{ $t('settings.semiAuto') }}</strong>：{{ $t('settings.autonomyLevel3') }}</li>
            <li><strong>4 - {{ $t('settings.fullyAuto') }}</strong>：{{ $t('settings.autonomyLevel4') }}</li>
          </ul>
        </div>
      </el-alert>

      <el-form
        :model="sovereigntySettings"
        label-width="160px"
        class="settings-form"
      >
        <el-form-item :label="$t('settings.aiAutonomyLevel')">
          <div class="autonomy-slider">
            <el-slider
              v-model="sovereigntySettings.ai_autonomy_level"
              :min="0"
              :max="4"
              :step="1"
              :marks="autonomyMarks"
              :format-tooltip="formatAutonomyLevel"
              @change="handleAutonomyChange"
            />
            <div class="autonomy-labels">
              <span
                v-for="label in autonomyLabels"
                :key="label"
                class="autonomy-label"
              >{{ label }}</span>
            </div>
          </div>
        </el-form-item>

        <el-form-item :label="$t('settings.recommended')">
          <el-alert
            :title="currentAutonomyDescription"
            :type="getAutonomyAlertType(sovereigntySettings.ai_autonomy_level)"
            :closable="false"
            show-icon
          />
        </el-form-item>

        <el-divider />

        <div class="switch-grid">
          <el-form-item :label="$t('settings.showConfidence')">
            <el-switch v-model="sovereigntySettings.show_confidence_indicator" />
          </el-form-item>
          <el-form-item :label="$t('settings.showAlternatives')">
            <el-switch v-model="sovereigntySettings.show_alternatives" />
          </el-form-item>
          <el-form-item :label="$t('settings.showReasoning')">
            <el-switch v-model="sovereigntySettings.show_reasoning" />
          </el-form-item>
          <el-form-item :label="$t('settings.predictConfirm')">
            <el-switch
              v-model="sovereigntySettings.require_confirmation_for_predict"
              :disabled="sovereigntySettings.ai_autonomy_level >= 3"
            />
          </el-form-item>
          <el-form-item :label="$t('settings.trainConfirm')">
            <el-switch
              v-model="sovereigntySettings.require_confirmation_for_train"
              :disabled="sovereigntySettings.ai_autonomy_level >= 4"
            />
          </el-form-item>
        </div>

        <div class="form-actions">
          <el-button
            type="primary"
            @click="saveSovereigntySettings"
          >
            <el-icon style="margin-right: 4px;">
              <Check />
            </el-icon>
            {{ $t('settings.saveSovereignty') }}
          </el-button>
          <el-button @click="resetSovereigntySettings">
            <el-icon style="margin-right: 4px;">
              <RefreshLeft />
            </el-icon>
            {{ $t('common.reset') }}
          </el-button>
        </div>
      </el-form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { MagicStick, Check, RefreshLeft } from '@element-plus/icons-vue'
import { useSovereigntySettings } from '@/composables/useSovereigntySettings'

defineProps<{
  showSovereigntyIntro: boolean
}>()

const emit = defineEmits<{
  'update:showSovereigntyIntro': [value: boolean]
}>()

const { t } = useI18n()

const {
  sovereigntySettings,
  autonomyMarks,
  formatAutonomyLevel,
  currentAutonomyDescription,
  getAutonomyAlertType,
  handleAutonomyChange,
  saveSovereigntySettings,
  resetSovereigntySettings,
} = useSovereigntySettings()

const autonomyLabels = computed(() => [
  t('settings.fullyManual'),
  t('settings.confirmRequired'),
  t('settings.recommended'),
  t('settings.semiAuto'),
  t('settings.fullyAuto'),
])
</script>

<style scoped>
.autonomy-slider {
  width: 100%;
}

.autonomy-labels {
  display: flex;
  justify-content: space-between;
  margin-top: 8px;
}

.autonomy-label {
  font-size: 12px;
  color: var(--text-secondary);
  text-align: center;
  flex: 1;
}

.switch-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 32px;
}

.form-actions {
  display: flex;
  gap: 8px;
  padding-top: 16px;
  margin-top: 8px;
  border-top: 1px solid var(--bg-100);
}

.settings-form :deep(.el-form-item) {
  margin-bottom: 18px;
}

.settings-form :deep(.el-divider) {
  border-color: var(--bg-100);
  margin: 4px 0;
}
</style>