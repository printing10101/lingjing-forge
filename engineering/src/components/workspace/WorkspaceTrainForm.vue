<template>
  <el-form :model="localForm" label-width="140px">
    <el-form-item :label="t('workspace.modelName')">
      <el-input
        v-model="localForm.modelName"
        :placeholder="t('workspace.modelNamePlaceholder')"
      />
    </el-form-item>
    <el-form-item :label="t('workspace.dataPath')">
      <el-input
        v-model="localForm.dataPath"
        :placeholder="t('workspace.dataPathPlaceholder')"
      />
    </el-form-item>
    <el-divider content-position="left">
      {{ t('workspace.hyperparams') }}
    </el-divider>
    <el-form-item :label="t('workspace.learningRate')">
      <el-input-number
        v-model="localForm.hyperparameters.learning_rate"
        :min="0.0001"
        :max="0.1"
        :step="0.001"
        :precision="4"
      />
    </el-form-item>
    <el-form-item :label="t('workspace.epochs')">
      <el-input-number
        v-model="localForm.hyperparameters.epochs"
        :min="1"
        :max="1000"
        :step="10"
      />
    </el-form-item>
    <el-form-item :label="t('workspace.batchSize')">
      <el-input-number
        v-model="localForm.hyperparameters.batch_size"
        :min="1"
        :max="256"
        :step="8"
      />
    </el-form-item>
    <el-form-item :label="t('workspace.optimizer')">
      <el-select v-model="localForm.hyperparameters.optimizer">
        <el-option label="Adam" value="adam" />
        <el-option label="SGD" value="sgd" />
        <el-option label="RMSprop" value="rmsprop" />
      </el-select>
    </el-form-item>
    <el-form-item :label="t('workspace.device')">
      <el-select v-model="localForm.device">
        <el-option :label="t('workspace.auto')" value="auto" />
        <el-option label="GPU (CUDA)" value="cuda" />
        <el-option :label="t('workspace.cpu')" value="cpu" />
      </el-select>
    </el-form-item>
    <el-form-item>
      <el-button
        type="warning"
        :loading="dryRunning"
        @click="$emit('dry-run')"
      >
        {{ t('workspace.previewPlan') }}
      </el-button>
      <el-button
        type="primary"
        :loading="training"
        :disabled="!trainPlanConfirmed"
        @click="$emit('train')"
      >
        {{ t('workspace.startTraining') }}
      </el-button>
    </el-form-item>
  </el-form>
</template>

<script setup lang="ts">
import { ref, watch, toRaw } from 'vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

interface Hyperparameters {
  learning_rate: number
  epochs: number
  batch_size: number
  optimizer: string
}

interface TrainForm {
  modelName: string
  dataPath: string
  hyperparameters: Hyperparameters
  device: string
}

const props = defineProps<{
  trainForm: TrainForm
  dryRunning: boolean
  training: boolean
  trainPlanConfirmed: boolean
}>()

const emit = defineEmits<{
  'dry-run': []
  'train': []
  'update:train-form': [form: TrainForm]
}>()

// 本地副本：表单编辑不直接变异 prop（修复 vue/no-mutating-props）
// 双向同步：父组件外部变更（重置等）→ 覆盖本地副本；本地编辑 → emit 回写
// toRaw：props 是 reactive proxy，structuredClone 直接克隆会抛 DataCloneError
const localForm = ref<TrainForm>(structuredClone(toRaw(props.trainForm)))

watch(
  () => props.trainForm,
  (val) => {
    if (JSON.stringify(toRaw(val)) !== JSON.stringify(localForm.value)) {
      localForm.value = structuredClone(toRaw(val))
    }
  },
  { deep: true },
)

watch(
  localForm,
  (val) => {
    if (JSON.stringify(toRaw(val)) !== JSON.stringify(toRaw(props.trainForm))) {
      emit('update:train-form', structuredClone(toRaw(val)))
    }
  },
  { deep: true },
)
</script>