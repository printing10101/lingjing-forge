<template>
  <div class="about-page">
    <el-card>
      <template #header>
        {{ $t('about.pageTitle') }}
      </template>
      <div class="about-content">
        <h3>{{ $t('about.productName') }}</h3>

        <div class="version-line">
          <span class="version-text">{{ $t('about.versionLabel') }}：<b>{{ currentVersion || '--' }}</b></span>
          <el-button
            size="small"
            type="primary"
            plain
            :loading="checking"
            data-testid="check-update-btn"
            @click="onCheckUpdate"
          >
            {{ checking ? $t('about.checking') : $t('about.checkUpdate') }}
          </el-button>
        </div>

        <el-alert
          v-if="result && !result.error"
          :title="resultMessage"
          :type="result.update_available ? 'warning' : 'success'"
          :closable="false"
          class="update-alert"
          data-testid="update-alert"
        >
          <el-button
            v-if="result.update_available"
            size="small"
            type="primary"
            class="download-btn"
            data-testid="download-btn"
            @click="openRelease"
          >
            {{ $t('about.goDownload') }}
          </el-button>
        </el-alert>
        <el-alert
          v-else-if="result?.error"
          :title="errorMessage"
          type="error"
          :closable="false"
          class="update-alert"
          data-testid="update-alert-error"
        />

        <p>{{ $t('about.description') }}</p>

        <el-divider />

        <h4>{{ $t('about.coreTech') }}</h4>
        <ul>
          <li>{{ $t('about.techLnn') }}</li>
          <li>{{ $t('about.techCfc') }}</li>
          <li>{{ $t('about.techLtc') }}</li>
          <li>{{ $t('about.techHybrid') }}</li>
          <li>{{ $t('about.techDempster') }}</li>
        </ul>

        <h4>{{ $t('about.dataSupport') }}</h4>
        <ul>
          <li>{{ $t('about.dataBosch') }}</li>
          <li>{{ $t('about.dataUniwearTc4') }}</li>
          <li>{{ $t('about.dataUniwearPhm') }}</li>
        </ul>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { checkForUpdates, getSystemVersion, type UpdateCheckResult } from '@/api/system'

const { t } = useI18n()

const currentVersion = ref('')
const checking = ref(false)
const result = ref<UpdateCheckResult | null>(null)

const resultMessage = computed(() => {
  if (!result.value) return ''
  if (result.value.update_available) {
    return t('about.updateAvailable', { version: result.value.latest_version ?? '' })
  }
  return t('about.upToDate')
})

const errorMessage = computed(() => {
  if (!result.value?.error) return t('about.checkFailed')
  const map: Record<string, string> = {
    network: t('about.errorNetwork'),
    parse: t('about.errorParse'),
  }
  return map[result.value.error] ?? t('about.checkFailed')
})

onMounted(async () => {
  try {
    const info = await getSystemVersion()
    currentVersion.value = info.version
  } catch {
    // 后端不可用时保持占位，不阻塞页面
  }
})

async function onCheckUpdate() {
  checking.value = true
  result.value = null
  try {
    result.value = await checkForUpdates()
  } catch {
    result.value = {
      current_version: currentVersion.value,
      latest_version: null,
      update_available: false,
      latest_release_url: null,
      checked_at: '',
      error: 'network',
    }
  } finally {
    checking.value = false
  }
}

function openRelease() {
  const url =
    result.value?.latest_release_url ||
    'https://github.com/printing10101/Virtual-Realm-Manufacturing/releases/latest'
  void openExternalUrl(url)
}

// 桌面模式：调用 Tauri 原生命令打开外部浏览器；Web/测试模式：回退 window.open
async function openExternalUrl(url: string) {
  try {
    const { invoke } = await import('@tauri-apps/api/core')
    await invoke('open_external_url', { url })
  } catch {
    window.open(url, '_blank', 'noopener')
  }
}
</script>

<style scoped>
.about-page {
  max-width: 800px;
  margin: 0 auto;
}

.about-content ul {
  padding-left: 20px;
  line-height: 1.8;
  color: var(--text-secondary);
}

.about-content h3 {
  color: var(--text-primary);
  font-weight: 600;
}

.about-content h4 {
  color: var(--text-primary);
  font-weight: 600;
  margin-top: 24px;
}

.about-content p {
  color: var(--text-secondary);
  line-height: 1.6;
}

.version-line {
  display: flex;
  align-items: center;
  gap: 12px;
}

.version-text b {
  color: var(--text-primary);
}

.update-alert {
  margin-top: 12px;
}

.download-btn {
  margin-left: 8px;
}
</style>
