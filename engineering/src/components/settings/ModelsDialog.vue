<template>
  <el-dialog
    :model-value="visible"
    :title="title"
    width="640px"
    :close-on-click-modal="false"
    append-to-body
    @update:model-value="onVisibleChange"
    @open="onOpen"
  >
    <div
      v-loading="loading"
      class="models-dialog-body"
    >
      <div
        v-if="!provider"
        class="empty-state"
      >
        <el-empty
          :description="t('settings.modelsDialog.emptyProvider')"
          :image-size="60"
        />
      </div>

      <template v-else>
        <!-- Provider 元信息 -->
        <el-descriptions
          :column="2"
          border
          size="small"
          class="provider-meta"
        >
          <el-descriptions-item label="Provider">
            {{ provider.name }}
            <span class="mono">({{ provider.provider_id }})</span>
          </el-descriptions-item>
          <el-descriptions-item :label="t('settings.modelsDialog.labelType')">
            <el-tag
              size="small"
              effect="plain"
            >
              {{ provider.provider_type }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item
            label="Base URL"
            :span="2"
          >
            <span class="mono">{{ provider.base_url || '-' }}</span>
          </el-descriptions-item>
          <el-descriptions-item :label="t('settings.modelsDialog.labelDefaultModel')">
            <span class="mono">{{ provider.default_model || '-' }}</span>
          </el-descriptions-item>
          <el-descriptions-item :label="t('settings.modelsDialog.labelStatus')">
            <el-tag
              :type="provider.enabled ? 'success' : 'info'"
              size="small"
            >
              {{ provider.enabled ? t('settings.modelsDialog.statusEnabled') : t('settings.modelsDialog.statusDisabled') }}
            </el-tag>
          </el-descriptions-item>
        </el-descriptions>

        <!-- 模型列表 -->
        <div class="models-toolbar">
          <span class="models-toolbar__count">
            {{ t('settings.modelsDialog.toolbarCount', { count: models.length }) }}
          </span>
          <el-input
            v-model="filter"
            size="small"
            clearable
            :placeholder="t('settings.modelsDialog.placeholderFilter')"
            style="width: 220px;"
          >
            <template #prefix>
              <el-icon><Search /></el-icon>
            </template>
          </el-input>
          <el-button
            size="small"
            :loading="loading"
            @click="loadModels"
          >
            <el-icon style="margin-right: 4px;">
              <Refresh />
            </el-icon>
            {{ t('settings.modelsDialog.btnRefresh') }}
          </el-button>
        </div>

        <el-table
          :data="filteredModels"
          size="small"
          stripe
          max-height="320"
          :empty-text="t('settings.modelsDialog.emptyNoModels')"
        >
          <el-table-column
            label="#"
            type="index"
            width="50"
          />
          <el-table-column
            :label="t('settings.modelsDialog.colModelId')"
            prop="id"
            min-width="200"
          >
            <template #default="{ row }">
              <span class="mono">{{ row.id }}</span>
              <el-tag
                v-if="row.id === provider.default_model"
                size="small"
                type="success"
                effect="plain"
                style="margin-left: 6px;"
              >
                {{ t('settings.modelsDialog.tagDefault') }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('settings.modelsDialog.colName')"
            prop="name"
            min-width="140"
          >
            <template #default="{ row }">
              {{ row.name || '-' }}
            </template>
          </el-table-column>
          <el-table-column
            :label="t('settings.modelsDialog.colOwner')"
            prop="owned_by"
            width="120"
          >
            <template #default="{ row }">
              {{ row.owned_by || '-' }}
            </template>
          </el-table-column>
          <el-table-column
            :label="t('settings.modelsDialog.colActions')"
            width="100"
            align="center"
          >
            <template #default="{ row }">
              <el-button
                size="small"
                text
                type="primary"
                @click="copyModelId(row.id)"
              >
                {{ t('settings.modelsDialog.btnCopyId') }}
              </el-button>
            </template>
          </el-table-column>
        </el-table>

        <!-- 错误提示 -->
        <el-alert
          v-if="errorMsg"
          :title="errorMsg"
          type="error"
          :closable="true"
          show-icon
          style="margin-top: 12px;"
          @close="errorMsg = ''"
        />
      </template>
    </div>

    <template #footer>
      <el-button @click="onClose">
        {{ t('settings.modelsDialog.btnClose') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Search, Refresh } from '@element-plus/icons-vue'
import { useLLMProvidersStore } from '@/stores/llmProviders'
import type { LLMProvider, ModelInfo } from '@/types/llmProvider'

const { t } = useI18n()

const props = defineProps<{
  visible: boolean
  provider: LLMProvider | null
}>()

const emit = defineEmits<{
  (e: 'update:visible', val: boolean): void
}>()

const store = useLLMProvidersStore()

const models = ref<ModelInfo[]>([])
const loading = ref(false)
const errorMsg = ref('')
const filter = ref('')

const title = computed(() => {
  if (!props.provider) return t('settings.modelsDialog.titleDefault')
  return t('settings.modelsDialog.titleSuffix', { name: props.provider.name })
})

const filteredModels = computed(() => {
  if (!filter.value.trim()) return models.value
  const q = filter.value.trim().toLowerCase()
  return models.value.filter(
    (m) =>
      m.id.toLowerCase().includes(q) ||
      (m.name?.toLowerCase().includes(q) ?? false),
  )
})

async function loadModels(): Promise<void> {
  if (!props.provider) return
  loading.value = true
  errorMsg.value = ''
  try {
    const result = await store.listModels(props.provider.provider_id)
    models.value = result
    if (result.length === 0) {
      errorMsg.value = t('settings.modelsDialog.errorZeroModels')
    }
  } catch (e: unknown) {
    errorMsg.value = (e as Error)?.message ?? t('settings.modelsDialog.errorLoadFailed')
    models.value = []
  } finally {
    loading.value = false
  }
}

function onOpen(): void {
  models.value = []
  errorMsg.value = ''
  filter.value = ''
  if (props.provider) {
    loadModels()
  }
}

function onVisibleChange(val: boolean): void {
  emit('update:visible', val)
}

function onClose(): void {
  emit('update:visible', false)
}

function copyModelId(id: string): void {
  navigator.clipboard
    ?.writeText(id)
    .then(() => {
      ElMessage.success(t('settings.modelsDialog.msgCopied', { id }))
    })
    .catch(() => {
      ElMessage.warning(t('settings.modelsDialog.warnClipboardUnavailable'))
    })
}

// 当 provider 变化时也重新加载（对话框已打开时切换 provider 的边界情况）
watch(
  () => props.provider?.provider_id,
  (id) => {
    if (id && props.visible) {
      loadModels()
    }
  },
)
</script>

<style scoped>
.models-dialog-body {
  min-height: 200px;
}

.empty-state {
  padding: 24px 0;
}

.provider-meta {
  margin-bottom: 16px;
}

.mono {
  font-family: var(--font-mono);
  font-size: 12px;
}

.models-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.models-toolbar__count {
  font-size: 13px;
  color: var(--text-secondary);
  font-weight: 500;
  margin-right: auto;
}
</style>
