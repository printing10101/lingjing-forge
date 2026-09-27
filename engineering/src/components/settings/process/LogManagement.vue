<template>
  <div class="content-card">
    <div class="content-card__header">
      <span class="content-card__title">
        <el-icon style="margin-right: 6px;"><Setting /></el-icon>
        {{ $t('settings.logManagement') }}
      </span>
      <el-button
        size="small"
        type="primary"
        :loading="exportingLogs"
        :disabled="exportingLogs"
        @click="$emit('export')"
      >
        <el-icon
          v-if="!exportingLogs"
          style="margin-right: 4px;"
        >
          <Download />
        </el-icon>
        {{ exportingLogs ? `${$t('settings.exporting')} ${exportProgress}%` : $t('settings.exportLogs') }}
      </el-button>
    </div>
    <div class="content-card__body">
      <el-form
        :model="logSettings"
        label-width="160px"
        class="settings-form"
      >
        <div class="form-grid">
          <el-form-item :label="$t('settings.logLevel')">
            <el-select
              :model-value="logSettings.logLevel"
              @update:model-value="$emit('update:logSettings', { ...logSettings, logLevel: $event })"
              style="width: 180px;"
            >
              <el-option label="DEBUG" value="DEBUG" />
              <el-option label="INFO" value="INFO" />
              <el-option label="WARN" value="WARN" />
              <el-option label="ERROR" value="ERROR" />
            </el-select>
          </el-form-item>
          <el-form-item :label="$t('settings.logMaxFileSize')">
            <div class="input-with-unit">
              <el-input-number
                :model-value="logSettings.maxFileSizeMB"
                @update:model-value="$emit('update:logSettings', { ...logSettings, maxFileSizeMB: $event! })"
                :min="10"
                :max="500"
                :step="10"
                style="width: 180px;"
              />
              <span class="input-unit">MB</span>
            </div>
          </el-form-item>
          <el-form-item :label="$t('settings.logRetentionDays')">
            <div class="input-with-unit">
              <el-input-number
                :model-value="logSettings.retentionDays"
                @update:model-value="$emit('update:logSettings', { ...logSettings, retentionDays: $event! })"
                :min="1"
                :max="365"
                :step="1"
                style="width: 180px;"
              />
              <span class="input-unit">{{ $t('settings.days') }}</span>
            </div>
          </el-form-item>
          <el-form-item :label="$t('settings.logExportDays')">
            <div class="input-with-unit">
              <el-input-number
                :model-value="logSettings.exportDays ?? 0"
                @update:model-value="$emit('update:logSettings', { ...logSettings, exportDays: $event! })"
                :min="1"
                :max="90"
                :step="1"
                style="width: 180px;"
              />
              <span class="input-unit">{{ $t('settings.days') }}</span>
            </div>
          </el-form-item>
        </div>

        <div class="form-actions">
          <el-button
            type="primary"
            @click="$emit('save')"
          >
            <el-icon style="margin-right: 4px;">
              <Check />
            </el-icon>
            {{ $t('settings.saveSettings') }}
          </el-button>
        </div>
      </el-form>

      <el-alert
        v-if="exportResult"
        :title="exportResult.success ? $t('settings.exportSuccess') : $t('settings.exportFailed')"
        :type="exportResult.success ? 'success' : 'error'"
        :closable="true"
        show-icon
        style="margin-top: 16px;"
        @close="$emit('closeExportResult')"
      >
        <div>
          <p>{{ exportResult.message }}</p>
          <p
            v-if="exportResult.outputPath"
            style="font-size: 12px; color: var(--info); word-break: break-all;"
          >
            {{ $t('settings.exportSavePath') }}: {{ exportResult.outputPath }}
          </p>
        </div>
      </el-alert>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Download, Check, Setting } from '@element-plus/icons-vue'
import type { LogSettings } from '@/stores/settings'
import type { LogExportResult } from '@/composables/useSettings'

defineProps<{
  logSettings: LogSettings
  exportingLogs: boolean
  exportProgress: number
  exportResult: LogExportResult | null
}>()

defineEmits<{
  'update:logSettings': [value: LogSettings]
  export: []
  save: []
  closeExportResult: []
}>()
</script>

<style scoped>
.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 32px;
}

.input-with-unit {
  display: flex;
  align-items: center;
  gap: 8px;
}

.input-unit {
  font-size: 12px;
  color: var(--text-tertiary);
  white-space: nowrap;
}

.form-actions {
  display: flex;
  gap: 8px;
  padding-top: 12px;
  margin-top: 8px;
  border-top: 1px solid var(--bg-100);
}

.settings-form :deep(.el-form-item) {
  margin-bottom: 18px;
}
</style>