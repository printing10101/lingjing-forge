<template>
  <el-dialog
    :model-value="visible"
    :title="mode === 'create' ? t('providerFormDialog.titleCreate') : t('providerFormDialog.titleEdit', { name: provider?.name })"
    width="640px"
    :close-on-click-modal="false"
    @update:model-value="$emit('update:visible', $event)"
    @open="handleOpen"
  >
    <el-form
      ref="formRef"
      :model="form"
      :rules="rules"
      label-width="120px"
      label-position="right"
    >
      <el-form-item
        :label="t('providerFormDialog.labelProviderId')"
        prop="provider_id"
      >
        <el-input
          v-model="form.provider_id"
          :placeholder="t('providerFormDialog.placeholderProviderId')"
          :disabled="mode === 'edit'"
        />
        <div class="form-tip">
          {{ t('providerFormDialog.tipProviderId') }}
        </div>
      </el-form-item>

      <el-form-item
        :label="t('providerFormDialog.labelDisplayName')"
        prop="name"
      >
        <el-input
          v-model="form.name"
          :placeholder="t('providerFormDialog.placeholderDisplayName')"
        />
      </el-form-item>

      <el-form-item
        :label="t('providerFormDialog.labelType')"
        prop="provider_type"
      >
        <el-select
          v-model="form.provider_type"
          :placeholder="t('providerFormDialog.placeholderSelectType')"
          :disabled="mode === 'edit'"
          style="width: 100%"
          @change="onTypeChange"
        >
          <el-option-group :label="t('providerFormDialog.groupLocal')">
            <el-option
              v-for="item in localTypes"
              :key="item.value"
              :label="`${item.label} 鈥?${item.description}`"
              :value="item.value"
            />
          </el-option-group>
          <el-option-group :label="t('providerFormDialog.groupCloud')">
            <el-option
              v-for="item in cloudTypes"
              :key="item.value"
              :label="`${item.label} 鈥?${item.description}`"
              :value="item.value"
            />
          </el-option-group>
        </el-select>
      </el-form-item>

      <el-form-item
        :label="t('providerFormDialog.labelBaseUrl')"
        prop="base_url"
      >
        <el-input
          v-model="form.base_url"
          :placeholder="urlPlaceholder"
        />
        <div class="form-tip">
          {{ urlTip }}
        </div>
      </el-form-item>

      <el-form-item
        v-if="needsApiKey"
        :label="t('providerFormDialog.labelApiKey')"
        prop="api_key"
      >
        <el-input
          v-model="form.api_key"
          type="password"
          show-password
          :placeholder="mode === 'edit' ? t('providerFormDialog.placeholderApiKeyEdit') : t('providerFormDialog.placeholderApiKeyCreate')"
        />
        <div class="form-tip">
          {{ mode === 'edit' ? t('providerFormDialog.tipApiKeyEdit') : t('providerFormDialog.tipApiKeyCreate') }}
        </div>
      </el-form-item>

      <el-form-item
        :label="t('providerFormDialog.labelDefaultModel')"
        prop="default_model"
      >
        <el-input
          v-model="form.default_model"
          :placeholder="t('providerFormDialog.placeholderDefaultModel')"
        />
        <div class="form-tip">
          {{ t('providerFormDialog.tipDefaultModel') }}
        </div>
      </el-form-item>

      <el-form-item
        :label="t('providerFormDialog.labelCapabilities')"
        prop="capabilities"
      >
        <el-select
          v-model="form.capabilities"
          multiple
          :placeholder="t('providerFormDialog.placeholderSelectCapabilities')"
          style="width: 100%"
        >
          <el-option
            :label="t('providerFormDialog.capabilityChat')"
            value="chat"
          />
          <el-option
            :label="t('providerFormDialog.capabilityStreaming')"
            value="streaming"
          />
          <el-option
            :label="t('providerFormDialog.capabilityFunctionCalling')"
            value="function_calling"
          />
          <el-option
            :label="t('providerFormDialog.capabilityVision')"
            value="vision"
          />
          <el-option
            :label="t('providerFormDialog.capabilityEmbeddings')"
            value="embeddings"
          />
        </el-select>
      </el-form-item>

      <el-form-item
        :label="t('providerFormDialog.labelPriority')"
        prop="priority"
      >
        <el-slider
          v-model="form.priority"
          :min="0"
          :max="100"
          show-input
          style="width: 100%"
        />
        <div class="form-tip">
          {{ t('providerFormDialog.tipPriority') }}
        </div>
      </el-form-item>

      <el-divider content-position="left">
        {{ t('providerFormDialog.labelAdvanced') }}
      </el-divider>

      <el-form-item
        :label="t('providerFormDialog.labelTimeout')"
        prop="timeout"
      >
        <el-input-number
          v-model="form.timeout"
          :min="5"
          :max="600"
        />
      </el-form-item>

      <el-form-item
        :label="t('providerFormDialog.labelMaxRetries')"
        prop="max_retries"
      >
        <el-input-number
          v-model="form.max_retries"
          :min="0"
          :max="10"
        />
      </el-form-item>

      <el-form-item
        :label="t('providerFormDialog.labelRetryDelay')"
        prop="retry_delay"
      >
        <el-input-number
          v-model="form.retry_delay"
          :min="0"
          :max="30"
          :step="0.5"
        />
      </el-form-item>

      <el-form-item
        :label="t('providerFormDialog.labelEnabled')"
        prop="enabled"
      >
        <el-switch v-model="form.enabled" />
        <div class="form-tip">
          {{ t('providerFormDialog.tipEnabled') }}
        </div>
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="$emit('update:visible', false)">
        {{ t('providerFormDialog.btnCancel') }}
      </el-button>
      <el-button
        type="primary"
        :loading="saving"
        @click="handleSave"
      >
        {{ mode === 'create' ? t('providerFormDialog.btnCreate') : t('providerFormDialog.btnSave') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { useLLMProvidersStore } from '@/stores/llmProviders'
import { PROVIDER_TYPE_META } from '@/api/llmProviders'
import type {
  LLMProvider,
  ProviderType,
  ProviderCapability,
  ProviderUpsertRequest,
} from '@/types/llmProvider'

const { t } = useI18n()

const props = defineProps<{
  visible: boolean
  mode: 'create' | 'edit'
  provider: LLMProvider | null
}>()

const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void
  (e: 'saved'): void
}>()

const store = useLLMProvidersStore()
const formRef = ref<FormInstance | null>(null)
const saving = ref(false)

interface FormState {
  provider_id: string
  name: string
  provider_type: ProviderType | ''
  base_url: string
  api_key: string
  default_model: string
  capabilities: ProviderCapability[]
  priority: number
  timeout: number
  max_retries: number
  retry_delay: number
  enabled: boolean
}

const form = reactive<FormState>({
  provider_id: '',
  name: '',
  provider_type: '',
  base_url: '',
  api_key: '',
  default_model: '',
  capabilities: ['chat'],
  priority: 0,
  timeout: 60,
  max_retries: 3,
  retry_delay: 1.0,
  enabled: true,
})

const rules: FormRules = {
  provider_id: [
    { required: true, message: () => t('providerFormDialog.ruleProviderIdRequired'), trigger: 'blur' },
    {
      pattern: /^[a-zA-Z0-9_-]+$/,
      message: () => t('providerFormDialog.ruleProviderIdPattern'),
      trigger: 'blur',
    },
  ],
  name: [{ required: true, message: () => t('providerFormDialog.ruleNameRequired'), trigger: 'blur' }],
  provider_type: [{ required: true, message: () => t('providerFormDialog.ruleTypeRequired'), trigger: 'change' }],
}

const localTypes = computed(() =>
  Object.values(PROVIDER_TYPE_META).filter((m) => m.category === 'local'),
)
const cloudTypes = computed(() =>
  Object.values(PROVIDER_TYPE_META).filter((m) => m.category === 'cloud'),
)

const selectedMeta = computed(() => {
  if (!form.provider_type) return null
  return PROVIDER_TYPE_META[form.provider_type as ProviderType] ?? null
})

const needsApiKey = computed(() => selectedMeta.value?.needs_api_key ?? false)

const urlPlaceholder = computed(() => {
  if (!selectedMeta.value) return 'https://...'
  return selectedMeta.value.default_base_url || 'https://...'
})

const urlTip = computed(() => {
  if (!selectedMeta.value) return t('providerFormDialog.tipBaseUrlDefault')
  return selectedMeta.value.description
})

function onTypeChange(type: ProviderType | ''): void {
  if (!type) return
  const meta = PROVIDER_TYPE_META[type]
  if (!meta) return
  if (!form.base_url) form.base_url = meta.default_base_url
  if (form.capabilities.length === 0 || form.capabilities[0] === 'chat') {
    form.capabilities = [...meta.default_capabilities]
  }
}

function handleOpen(): void {
  if (props.mode === 'edit' && props.provider) {
    const p = props.provider
    form.provider_id = p.provider_id
    form.name = p.name
    form.provider_type = p.provider_type
    form.base_url = p.base_url
    form.api_key = ''
    form.default_model = p.default_model
    form.capabilities = [...p.capabilities]
    form.priority = p.priority
    form.timeout = p.timeout
    form.max_retries = p.max_retries
    form.retry_delay = p.retry_delay
    form.enabled = p.enabled
  } else {
    form.provider_id = ''
    form.name = ''
    form.provider_type = ''
    form.base_url = ''
    form.api_key = ''
    form.default_model = ''
    form.capabilities = ['chat']
    form.priority = 0
    form.timeout = 60
    form.max_retries = 3
    form.retry_delay = 1.0
    form.enabled = true
  }
}

watch(
  () => props.visible,
  (v) => {
    if (v) handleOpen()
  },
)

async function handleSave(): Promise<void> {
  if (!formRef.value) return
  try {
    await formRef.value.validate()
  } catch {
    return
  }

  saving.value = true
  try {
    if (form.provider_type === '') {
      return
    }
    const payload: ProviderUpsertRequest = {
      provider_id: form.provider_id,
      name: form.name,
      provider_type: form.provider_type as ProviderType,
      base_url: form.base_url,
      default_model: form.default_model,
      timeout: form.timeout,
      max_retries: form.max_retries,
      retry_delay: form.retry_delay,
      enabled: form.enabled,
      priority: form.priority,
      capabilities: form.capabilities,
    }
    if (form.api_key) {
      payload.api_key = form.api_key
    }

    if (props.mode === 'create') {
      const created = await store.createProvider(payload)
      if (created) {
        emit('saved')
      }
    } else {
      const updated = await store.updateProvider(form.provider_id, payload)
      if (updated) {
        emit('saved')
      }
    }
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.form-tip {
  font-size: 11px;
  color: var(--text-tertiary);
  margin-top: 4px;
  line-height: 1.4;
}
</style>