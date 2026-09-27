<template>
  <el-dialog
    v-model="visible"
    :title="$t('feedRateDialog.title')"
    width="400px"
    :close-on-click-modal="false"
    @close="handleClose"
  >
    <el-form
      label-width="100px"
      size="default"
    >
      <el-form-item :label="$t('feedRateDialog.labelCurrentSegment')">
        <el-tag type="info">
          {{ segmentInfo }}
        </el-tag>
      </el-form-item>
      <el-form-item :label="$t('feedRateDialog.labelCurrentFeedRate')">
        <span class="current-value">{{ currentFeedRate }} {{ $t('toolpathEditor.unitFeedRate') }}</span>
      </el-form-item>
      <el-form-item :label="$t('feedRateDialog.labelNewFeedRate')">
        <el-input-number
          v-model="newFeedRate"
          :min="10"
          :max="50000"
          :step="10"
          controls-position="right"
          style="width: 100%"
        />
      </el-form-item>
      <el-form-item :label="$t('feedRateDialog.labelRange')">
        <el-alert
          :title="$t('feedRateDialog.rangeAlert')"
          type="info"
          :closable="false"
          show-icon
        />
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="visible = false">
        {{ $t('common.cancel') }}
      </el-button>
      <el-button
        type="primary"
        :disabled="newFeedRate === currentFeedRate || newFeedRate < 10 || newFeedRate > 50000"
        @click="handleConfirm"
      >
        {{ $t('feedRateDialog.confirm') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
const props = defineProps<{
  segmentId: string
  currentFeedRate: number
  segmentType: string
  segmentBlock: number
}>()

const emit = defineEmits<{
  'confirm': [segmentId: string, newFeedRate: number]
}>()

const visible = defineModel<boolean>('visible', { required: true })
const newFeedRate = ref(props.currentFeedRate)

const segmentInfo = computed(
  () => `#${props.segmentBlock} (${props.segmentType})`,
)

watch(
  () => props.currentFeedRate,
  (v) => {
    newFeedRate.value = v
  },
)

function handleConfirm() {
  emit('confirm', props.segmentId, newFeedRate.value)
  visible.value = false
}

function handleClose() {
  newFeedRate.value = props.currentFeedRate
}
</script>

<style lang="scss" scoped>
.current-value {
  font-weight: 600;
  color: var(--accent-primary);
}
</style>
