<template>
  <div class="tab-panel">
    <div class="export-section">
      <div class="export-grid">
        <div class="export-card">
          <h4 class="export-card-title">
            {{ t('simulationPage.exportGifTitle') }}
          </h4>
          <p class="export-card-desc">
            {{ t('simulationPage.exportGifDesc') }}
          </p>
          <el-form
            label-position="top"
            class="export-form"
          >
            <el-form-item :label="t('simulationPage.exportResolution')">
              <el-select
                :model-value="gifExport.resolution"
                size="small"
                style="width: 100%"
                @update:model-value="updateGifExport('resolution', $event)"
              >
                <el-option
                  label="640 x 360"
                  value="640x360"
                />
                <el-option
                  label="1280 x 720"
                  value="1280x720"
                />
                <el-option
                  label="1920 x 1080"
                  value="1920x1080"
                />
              </el-select>
            </el-form-item>
            <el-form-item :label="t('simulationPage.exportFramerate')">
              <el-select
                :model-value="gifExport.framerate"
                size="small"
                style="width: 100%"
                @update:model-value="updateGifExport('framerate', $event)"
              >
                <el-option
                  label="10 fps"
                  :value="10"
                />
                <el-option
                  label="15 fps"
                  :value="15"
                />
                <el-option
                  label="24 fps"
                  :value="24"
                />
                <el-option
                  label="30 fps"
                  :value="30"
                />
              </el-select>
            </el-form-item>
            <el-form-item :label="t('simulationPage.exportQuality')">
              <el-select
                :model-value="gifExport.quality"
                size="small"
                style="width: 100%"
                @update:model-value="updateGifExport('quality', $event)"
              >
                <el-option
                  :label="t('simulationPage.qualityLow')"
                  value="low"
                />
                <el-option
                  :label="t('simulationPage.qualityMedium')"
                  value="medium"
                />
                <el-option
                  :label="t('simulationPage.qualityHigh')"
                  value="high"
                />
              </el-select>
            </el-form-item>
          </el-form>
          <el-button
            type="primary"
            size="small"
            class="btn-export"
            :loading="exportLoading === 'gif'"
            @click="emit('export-gif')"
          >
            {{ t('simulationPage.exportGifBtn') }}
          </el-button>
        </div>
        <div class="export-card">
          <h4 class="export-card-title">
            {{ t('simulationPage.exportMp4Title') }}
          </h4>
          <p class="export-card-desc">
            {{ t('simulationPage.exportMp4Desc') }}
          </p>
          <el-form
            label-position="top"
            class="export-form"
          >
            <el-form-item :label="t('simulationPage.exportResolution')">
              <el-select
                :model-value="mp4Export.resolution"
                size="small"
                style="width: 100%"
                @update:model-value="updateMp4Export('resolution', $event)"
              >
                <el-option
                  label="1280 x 720"
                  value="1280x720"
                />
                <el-option
                  label="1920 x 1080"
                  value="1920x1080"
                />
                <el-option
                  label="2560 x 1440"
                  value="2560x1440"
                />
                <el-option
                  label="3840 x 2160"
                  value="3840x2160"
                />
              </el-select>
            </el-form-item>
            <el-form-item :label="t('simulationPage.exportFramerate')">
              <el-select
                :model-value="mp4Export.framerate"
                size="small"
                style="width: 100%"
                @update:model-value="updateMp4Export('framerate', $event)"
              >
                <el-option
                  label="24 fps"
                  :value="24"
                />
                <el-option
                  label="30 fps"
                  :value="30"
                />
                <el-option
                  label="60 fps"
                  :value="60"
                />
              </el-select>
            </el-form-item>
            <div class="export-form-row">
              <el-form-item
                :label="t('simulationPage.exportCodec')"
                class="form-item-half"
              >
                <el-select
                  :model-value="mp4Export.codec"
                  size="small"
                  style="width: 100%"
                  @update:model-value="updateMp4Export('codec', $event)"
                >
                  <el-option
                    label="H.264"
                    value="h264"
                  />
                  <el-option
                    label="H.265"
                    value="h265"
                  />
                  <el-option
                    label="AV1"
                    value="av1"
                  />
                </el-select>
              </el-form-item>
              <el-form-item
                :label="t('simulationPage.exportBitrate')"
                class="form-item-half"
              >
                <el-select
                  :model-value="mp4Export.bitrate"
                  size="small"
                  style="width: 100%"
                  @update:model-value="updateMp4Export('bitrate', $event)"
                >
                  <el-option
                    label="2 Mbps"
                    value="2"
                  />
                  <el-option
                    label="5 Mbps"
                    value="5"
                  />
                  <el-option
                    label="10 Mbps"
                    value="10"
                  />
                  <el-option
                    label="20 Mbps"
                    value="20"
                  />
                </el-select>
              </el-form-item>
            </div>
          </el-form>
          <el-button
            type="primary"
            size="small"
            class="btn-export"
            :loading="exportLoading === 'mp4'"
            @click="emit('export-mp4')"
          >
            {{ t('simulationPage.exportMp4Btn') }}
          </el-button>
        </div>
      </div>
      <div class="content-card">
        <div class="content-card__header">
          <span class="content-card__title">{{ t('simulationPage.exportHistoryTitle') }}</span>
        </div>
        <div class="content-card__body">
          <el-empty
            :description="t('simulationPage.noExportHistory')"
            :image-size="60"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

// ─── Types ──────────────────────────────────────────────

export interface GifExportConfig {
  resolution: string
  framerate: number
  quality: string
}

export interface Mp4ExportConfig {
  resolution: string
  framerate: number
  codec: string
  bitrate: string
}

// ─── Props ──────────────────────────────────────────────

const props = defineProps<{
  gifExport: GifExportConfig
  mp4Export: Mp4ExportConfig
  exportLoading: 'gif' | 'mp4' | null
}>()

const emit = defineEmits<{
  'update:gifExport': [value: GifExportConfig]
  'update:mp4Export': [value: Mp4ExportConfig]
  'export-gif': []
  'export-mp4': []
}>()

// ─── Update Handlers ─────────────────────────────────────

function updateGifExport<K extends keyof GifExportConfig>(key: K, value: GifExportConfig[K]) {
  emit('update:gifExport', { ...props.gifExport, [key]: value })
}

function updateMp4Export<K extends keyof Mp4ExportConfig>(key: K, value: Mp4ExportConfig[K]) {
  emit('update:mp4Export', { ...props.mp4Export, [key]: value })
}
</script>

<style scoped>
/* ─── Tab Panel ────────────────────────────────────────── */
.tab-panel {
  animation: fadeIn 0.25s ease;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); }
}

/* ─── Content Card overrides (minimal) ─────────────────── */
.content-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 20px;
  border-bottom: 1px solid var(--bg-200);
}

.content-card__title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.content-card__body {
  padding: 16px 20px;
}

/* ─── Export Section ────────────────────────────────────── */
.export-section {
  /* no extra styles needed */
}

.export-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 20px;
  margin-bottom: 32px;
}

.export-card {
  background: var(--bg-100);
  border-radius: var(--radius-md);
  border: 1px solid var(--bg-200);
  padding: 24px;
  transition: border-color 0.25s ease, box-shadow 0.25s ease;
}

.export-card:hover {
  border-color: var(--accent-primary);
  box-shadow: var(--shadow-sm);
}

.export-card-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 8px;
}

.export-card-desc {
  font-size: 13px;
  color: var(--text-secondary);
  margin: 0 0 16px;
  line-height: 1.6;
}

:deep(.export-form .el-form-item__label) {
  color: var(--text-secondary);
  font-size: 13px;
}

.export-form-row {
  display: flex;
  gap: 16px;
}

.form-item-half {
  flex: 1;
}

.btn-export {
  width: 100%;
  font-weight: 500;
}
</style>