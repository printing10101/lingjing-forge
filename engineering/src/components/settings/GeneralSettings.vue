<template>
  <div class="general-settings">
    <!-- 版本不一致警告 -->
    <el-alert
      v-if="versionStore.inconsistencyDetails && !versionStore.isConsistent"
      :title="$t('settings.versionWarningTitle')"
      type="error"
      :closable="false"
      show-icon
      class="version-warning"
    >
      <div>
        {{ $t('settings.versionWarningMsg') }}
        <ul v-if="versionStore.inconsistencyDetails">
          <li
            v-for="(detail, idx) in versionStore.inconsistencyDetails"
            :key="`det-${idx}`"
          >
            {{ detail }}
          </li>
        </ul>
      </div>
    </el-alert>

    <!-- 版本信息 - stat-card 网格 -->
    <div class="content-card">
      <div class="content-card__header">
        <span class="content-card__title">
          <el-icon style="margin-right: 6px;"><InfoFilled /></el-icon>
          {{ $t('settings.versionInfo') }}
        </span>
        <el-tag
          :type="versionStore.isConsistent ? 'success' : 'danger'"
          size="small"
        >
          {{ versionStore.isConsistent ? $t('settings.versionConsistent') : $t('settings.versionInconsistent') }}
        </el-tag>
      </div>
      <div class="content-card__body">
        <div class="version-grid">
          <div class="version-card">
            <div class="version-card__icon">
              <el-icon :size="24">
                <Monitor />
              </el-icon>
            </div>
            <div class="version-card__info">
              <span class="version-card__label">{{ $t('settings.frontendVersion') }}</span>
              <span class="version-card__value">{{ versionStore.frontendVersion }}</span>
              <span
                v-if="versionStore.frontendCommit"
                class="version-card__hash"
              >{{ versionStore.frontendCommit }}</span>
            </div>
          </div>
          <div class="version-card">
            <div class="version-card__icon">
              <el-icon :size="24">
                <Cpu />
              </el-icon>
            </div>
            <div class="version-card__info">
              <span class="version-card__label">{{ $t('settings.rustBackendVersion') }}</span>
              <span class="version-card__value">{{ versionStore.rustVersion || $t('settings.loading') }}</span>
              <span
                v-if="versionStore.rustCommit"
                class="version-card__hash"
              >{{ versionStore.rustCommit }}</span>
            </div>
          </div>
          <div class="version-card">
            <div class="version-card__icon">
              <el-icon :size="24">
                <Coin />
              </el-icon>
            </div>
            <div class="version-card__info">
              <span class="version-card__label">{{ $t('settings.pythonSidecarVersion') }}</span>
              <span class="version-card__value">{{ versionStore.pythonVersion || $t('settings.notConnected') }}</span>
              <span
                v-if="versionStore.pythonCommit"
                class="version-card__hash"
              >{{ versionStore.pythonCommit }}</span>
            </div>
          </div>
          <div class="version-card version-card--action">
            <el-button
              size="small"
              :loading="versionStore.isLoading"
              @click="refreshVersions"
            >
              <el-icon
                v-if="!versionStore.isLoading"
                style="margin-right: 4px;"
              >
                <Refresh />
              </el-icon>
              {{ $t('settings.refreshVersion') }}
            </el-button>
          </div>
        </div>
      </div>
    </div>

    <!-- 系统设置 -->
    <div class="content-card">
      <div class="content-card__header">
        <span class="content-card__title">
          <el-icon style="margin-right: 6px;"><Setting /></el-icon>
          {{ $t('settings.systemSettings') }}
        </span>
      </div>
      <div class="content-card__body">
        <el-form
          :model="store.settings"
          label-width="140px"
          class="settings-form"
        >
          <div class="form-section">
            <div class="form-section__title">
              <el-icon><Connection /></el-icon>
              <span>{{ $t('settings.aiModeConfig') }}</span>
            </div>
            <el-form-item :label="$t('settings.aiMode')">
              <el-radio-group v-model="store.settings.aiMode">
                <el-radio value="local">
                  {{ $t('settings.localMode') }}
                </el-radio>
                <el-radio value="cloud">
                  {{ $t('settings.cloudMode') }}
                </el-radio>
              </el-radio-group>
            </el-form-item>
            <el-form-item :label="$t('settings.localModel')">
              <el-select
                v-model="store.settings.localModel"
                style="width: 200px;"
              >
                <el-option
                  label="qwen3.5:35b-128k"
                  value="qwen3.5:35b-128k"
                />
              </el-select>
            </el-form-item>
            <el-form-item :label="$t('settings.computeDevice')">
              <el-select
                v-model="store.settings.device"
                style="width: 200px;"
              >
                <el-option
                  label="CPU"
                  value="cpu"
                />
                <el-option
                  :label="$t('settings.gpuCuda')"
                  value="cuda"
                />
              </el-select>
            </el-form-item>
          </div>

          <el-divider />

          <!-- [U-P0-2] 硬件档位配置分区 -->
          <div class="form-section">
            <div class="form-section__title">
              <el-icon><Cpu /></el-icon>
              <span>{{ $t('settings.hardwareTierConfig') }}</span>
            </div>
            <el-form-item :label="$t('settings.hardwareTier')">
              <el-select
                v-model="store.settings.hardwareTier"
                style="width: 240px;"
                @change="handleHardwareTierChange"
              >
                <el-option
                  :label="$t('settings.hardwareTierMinimal')"
                  value="minimal"
                />
                <el-option
                  :label="$t('settings.hardwareTierStandard')"
                  value="standard"
                />
                <el-option
                  :label="$t('settings.hardwareTierHigh')"
                  value="high"
                />
                <el-option
                  :label="$t('settings.hardwareTierUltra')"
                  value="ultra"
                />
              </el-select>
            </el-form-item>
            <el-form-item>
              <el-alert
                :title="hardwareTierDescription"
                type="info"
                :closable="false"
                show-icon
              />
            </el-form-item>
            <el-form-item :label="$t('settings.lightweightMode')">
              <el-switch
                v-model="store.settings.lightweightMode"
                :disabled="store.settings.hardwareTier === 'minimal'"
              />
              <span class="form-hint">{{ $t('settings.lightweightModeDesc') }}</span>
            </el-form-item>
            <el-form-item v-if="store.settings.hardwareTier === 'minimal'">
              <el-alert
                :title="$t('settings.lightweightModeAutoEnabled')"
                type="warning"
                :closable="false"
                show-icon
              />
            </el-form-item>
            <el-form-item>
              <el-button
                size="small"
                :loading="syncingEnv"
                @click="handleSyncEnv"
              >
                <el-icon style="margin-right: 4px;"><Refresh /></el-icon>
                {{ $t('settings.hardwareTierSyncEnv') }}
              </el-button>
              <span class="form-hint">{{ $t('settings.hardwareTierSyncEnvDesc') }}</span>
            </el-form-item>
            <el-form-item>
              <el-alert
                :title="$t('settings.hardwareTierChangeHint')"
                type="warning"
                :closable="false"
                show-icon
              />
            </el-form-item>
          </div>

          <el-divider />

          <div class="form-section">
            <div class="form-section__title">
              <el-icon><Tools /></el-icon>
              <span>{{ $t('settings.generalPreferences') }}</span>
            </div>
            <el-form-item :label="$t('settings.offlineMode')">
              <el-switch v-model="store.settings.offlineMode" />
            </el-form-item>
            <el-form-item :label="$t('settings.language')">
              <el-select
                v-model="currentLocale"
                style="width: 200px;"
                @change="handleLocaleChange"
              >
                <el-option
                  :label="$t('settings.languageChinese')"
                  value="zh-CN"
                />
                <el-option
                  :label="$t('settings.languageEnglish')"
                  value="en"
                />
              </el-select>
            </el-form-item>
          </div>

          <div class="form-actions">
            <el-button
              type="primary"
              @click="store.saveSettings()"
            >
              <el-icon style="margin-right: 4px;">
                <Check />
              </el-icon>
              {{ $t('settings.saveSettings') }}
            </el-button>
            <el-button @click="store.resetSettings()">
              <el-icon style="margin-right: 4px;">
                <RefreshLeft />
              </el-icon>
              {{ $t('common.reset') }}
            </el-button>
          </div>
        </el-form>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessageBox, ElMessage } from 'element-plus'
import { useSettingsStore } from '@/stores/settings'
import { useVersionStore } from '@/stores/version'
import { useSettings } from '@/composables/useSettings'
import { useI18n } from 'vue-i18n'
import {
  InfoFilled, Monitor, Cpu, Coin, Refresh,
  Setting, Connection, Tools, Check, RefreshLeft
} from '@element-plus/icons-vue'

const store = useSettingsStore()
const versionStore = useVersionStore()
const { currentLocale, handleLocaleChange } = useSettings()
const { t } = useI18n()

// [U-P0-2] 硬件档位相关状态与逻辑
const syncingEnv = ref(false)

const hardwareTierDescription = computed(() => {
  const tier = store.settings.hardwareTier
  switch (tier) {
    case 'minimal': return t('settings.hardwareTierMinimalDesc')
    case 'standard': return t('settings.hardwareTierStandardDesc')
    case 'high': return t('settings.hardwareTierHighDesc')
    case 'ultra': return t('settings.hardwareTierUltraDesc')
    default: return ''
  }
})

/**
 * 硬件档位变更处理：
 *  - minimal 档位自动启用轻量模式（与后端 HardwareTierConfig.__post_init__ 派生逻辑一致）
 *  - 切换离开 minimal 时不清除 lightweightMode（用户可显式关闭）
 */
function handleHardwareTierChange(value: string) {
  if (value === 'minimal') {
    store.settings.lightweightMode = true
  }
  store.saveSettings()
}

/**
 * 同步环境变量到 .env 文件：
 *  由于 Tauri 端暂无写 .env 的 IPC 命令，此处采用降级方案——
 *  弹出 ElMessageBox 展示要写入的环境变量内容，用户可复制手动粘贴到 .env 文件。
 *  这样既不引入未实现的 IPC 调用，又保证用户能获得明确的配置指引。
 */
async function handleSyncEnv() {
  syncingEnv.value = true
  try {
    const tier = store.settings.hardwareTier
    const lightweight = store.settings.lightweightMode
    // minimal 档位后端会自动派生 skip_ollama=true，其他档位根据 lightweight_mode 决定
    const skipOllama = tier === 'minimal' || lightweight
    const content = [
      '# [U-P0-2] 硬件档位配置（由前端设置同步生成）',
      `LNN_HARDWARE_TIER=${tier}`,
      `LNN_LIGHTWEIGHT_MODE=${lightweight ? 'true' : 'false'}`,
      `LNN_SKIP_OLLAMA=${skipOllama ? 'true' : 'false'}`,
      `LNN_MAX_CONCURRENT_AI=${lightweight ? '1' : '2'}`,
    ].join('\n')

    await ElMessageBox.alert(
      `<pre style="background:var(--bg-secondary);padding:12px;border-radius: var(--radius-xs);font-family: var(--font-mono);font-size:13px;white-space:pre-wrap;word-break:break-all;">${content}</pre>`,
      t('settings.hardwareTierSyncEnvDesc'),
      {
        dangerouslyUseHTMLString: true,
        confirmButtonText: t('common.confirm'),
      }
    )
    ElMessage.success(t('settings.hardwareTierSyncSuccess'))
  } catch {
    // 用户取消弹窗时不显示错误
  } finally {
    syncingEnv.value = false
  }
}

function refreshVersions() {
  versionStore.fetchVersionInfo()
  versionStore.checkConsistency()
}
</script>

<style scoped>
.version-warning {
  margin-bottom: 16px;
}

.version-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr) auto;
  gap: 12px;
  align-items: stretch;
}

.version-card {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 16px;
  background: var(--bg-50);
  border-radius: var(--radius-md);
  border: 1px solid var(--bg-100);
  transition: border-color 0.2s;
}

.version-card:hover {
  border-color: var(--bg-200);
}

.version-card--action {
  display: flex;
  align-items: center;
  justify-content: center;
}

.version-card__icon {
  width: 44px;
  height: 44px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  background: var(--bg-0);
  color: var(--brand-500);
  flex-shrink: 0;
  border: 1px solid var(--bg-100);
}

.version-card__info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.version-card__label {
  font-size: 12px;
  color: var(--text-tertiary);
}

.version-card__value {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.version-card__hash {
  font-size: 11px;
  color: var(--text-tertiary);
  font-family: var(--font-mono);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.form-section {
  padding: 4px 0;
}

.form-section__title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 16px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--bg-100);
}

/* [U-P0-2] 表单项内联提示文字 */
.form-hint {
  margin-left: 12px;
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: 1.5;
}

.form-section__title .el-icon {
  color: var(--brand-500);
  font-size: 16px;
}

.form-actions {
  display: flex;
  gap: 8px;
  padding-top: 8px;
  margin-top: 8px;
  border-top: 1px solid var(--bg-100);
}

.settings-form :deep(.el-form-item) {
  margin-bottom: 20px;
}

.settings-form :deep(.el-divider) {
  border-color: var(--bg-100);
  margin: 4px 0;
}
</style>
