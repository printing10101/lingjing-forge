<template>
  <el-dialog
    v-model="visible"
    :title="$t('gcodeExport.title')"
    width="700px"
    :close-on-click-modal="false"
  >
    <el-form
      label-width="100px"
      size="default"
    >
      <el-form-item :label="$t('gcodeExport.labelController')">
        <el-select
          v-model="controller"
          style="width: 200px"
        >
          <el-option
            :label="$t('gcodeExport.controllerFanuc')"
            value="fanuc"
          />
          <el-option
            :label="$t('gcodeExport.controllerSiemens')"
            value="siemens"
          />
          <el-option
            :label="$t('gcodeExport.controllerHeidenhain')"
            value="heidenhain"
          />
        </el-select>
      </el-form-item>
      <el-form-item :label="$t('gcodeExport.labelProgramNumber')">
        <el-input-number
          v-model="programNumber"
          :min="1"
          :max="9999"
          controls-position="right"
          style="width: 200px"
        />
      </el-form-item>
    </el-form>

    <div
      v-if="validation && !validation.valid"
      class="validation-section"
    >
      <el-alert
        v-for="(err, i) in validation.errors"
        :key="'e' + i"
        :title="err"
        type="error"
        :closable="false"
        show-icon
        style="margin-bottom: 4px"
      />
    </div>

    <div
      v-if="validation && validation.warnings.length > 0"
      class="validation-section"
    >
      <el-alert
        v-for="(w, i) in validation.warnings"
        :key="'w' + i"
        :title="w"
        type="warning"
        :closable="false"
        show-icon
        style="margin-bottom: 4px"
      />
    </div>

    <el-input
      v-model="gcode"
      type="textarea"
      :rows="16"
      readonly
      style="margin-top: 12px; font-family: var(--font-mono); font-size: 12px"
    />

    <template #footer>
      <el-button @click="visible = false">
        {{ $t('gcodeExport.close') }}
      </el-button>
      <el-button
        type="primary"
        @click="handleCopy"
      >
        <el-icon><DocumentCopy /></el-icon>
        {{ $t('gcodeExport.copy') }}
      </el-button>
      <el-button
        type="success"
        @click="handleDownload"
      >
        <el-icon><Download /></el-icon>
        {{ $t('gcodeExport.download') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { DocumentCopy, Download } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { triggerFileDownload } from '@/utils/download'
import type { GCodeController } from './types/editor'
import { useToolpathEditorStore } from './stores/toolpathEditor'

const { t } = useI18n()
const visible = defineModel<boolean>('visible', { required: true })

const store = useToolpathEditorStore()
const controller = ref<GCodeController>('fanuc')
const programNumber = ref(1)
const gcode = ref('')
const validation = ref<{ valid: boolean; errors: string[]; warnings: string[] } | null>(null)

watch(visible, (v) => {
  if (v) {
    refreshExport()
  }
})

watch([controller, programNumber], () => {
  refreshExport()
})

function refreshExport() {
  const result = store.exportGCode(controller.value, programNumber.value)
  gcode.value = result.gcode
  validation.value = result.validation
}

async function handleCopy() {
  try {
    await navigator.clipboard.writeText(gcode.value)
    ElMessage.success(t('gcodeExport.copiedSuccess'))
  } catch {
    ElMessage.error(t('gcodeExport.copyFailed'))
  }
}

function handleDownload() {
  const ext = controller.value === 'heidenhain' ? '.h' : '.nc'
  const filename = `O${String(programNumber.value).padStart(4, '0')}${ext}`
  const blob = new Blob([gcode.value], { type: 'text/plain' })
  triggerFileDownload(blob, filename)
  ElMessage.success(t('gcodeExport.downloaded', { filename }))
}
</script>

<style lang="scss" scoped>
.validation-section {
  margin-bottom: 8px;
}
</style>
