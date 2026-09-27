<template>
  <el-dialog
    :model-value="visible"
    :title="plugin?.metadata?.name || t('pluginManager.detailDialogTitle')"
    width="800px"
    @update:model-value="emit('update:visible', $event)"
  >
    <div v-if="plugin" class="plugin-detail">
      <el-descriptions :column="2" border>
        <el-descriptions-item :label="t('pluginManager.detailId')">
          {{ plugin.metadata.id }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('pluginManager.detailVersion')">
          {{ plugin.metadata.version }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('pluginManager.detailAuthor')">
          {{ plugin.metadata.author }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('pluginManager.detailStatus')">
          <el-tag :type="statusType">
            {{ plugin.metadata.status }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item :label="t('pluginManager.detailType')">
          {{ plugin.metadata.plugin_type }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('pluginManager.detailCompatibility')">
          {{ plugin.metadata.min_core_version }} - {{ plugin.metadata.max_core_version }}
        </el-descriptions-item>
      </el-descriptions>

      <div class="section">
        <h4>{{ t('pluginManager.sectionDescription') }}</h4>
        <p>{{ plugin.metadata.description }}</p>
      </div>

      <div class="section">
        <h4>{{ t('pluginManager.sectionCapabilities') }}</h4>
        <el-tag
          v-for="cap in plugin.capabilities"
          :key="cap"
          style="margin-right: 5px; margin-bottom: 5px"
        >
          {{ cap }}
        </el-tag>
      </div>

      <div class="section">
        <h4>{{ t('pluginManager.sectionDependencies') }}</h4>
        <DependencyTree
          v-if="plugin.dependency_tree"
          :tree="plugin.dependency_tree"
        />
      </div>

      <div v-if="plugin.worker" class="section">
        <h4>{{ t('pluginManager.sectionWorkerInfo') }}</h4>
        <el-descriptions :column="2" border>
          <el-descriptions-item :label="t('pluginManager.detailWorkerStatus')">
            {{ plugin.worker.status }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('pluginManager.detailPid')">
            {{ plugin.worker.pid }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('pluginManager.detailPort')">
            {{ plugin.worker.port }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('pluginManager.detailUptime')">
            {{ formatUptime(plugin.worker.uptime) }}
          </el-descriptions-item>
        </el-descriptions>
      </div>

      <div class="section">
        <h4>{{ t('pluginManager.sectionConfig') }}</h4>
        <el-input
          v-model="configJson"
          type="textarea"
          :rows="5"
          @blur="handleConfigChange"
        />
      </div>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { usePluginStore } from '../../stores/plugin'
import type { PluginDetail } from '../../stores/plugin'
import DependencyTree from './DependencyTree.vue'

const { t } = useI18n()
const pluginStore = usePluginStore()

const props = defineProps<{
  visible: boolean
  plugin: PluginDetail | null
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
}>()

const configJson = ref('{}')

watch(() => props.plugin, (plugin) => {
  configJson.value = JSON.stringify(plugin?.metadata.config || {}, null, 2)
}, { immediate: true })

const statusType = computed(() => {
  const status = props.plugin?.metadata?.status
  switch (status) {
    case 'enabled':
      return 'success'
    case 'disabled':
      return 'warning'
    case 'error':
      return 'danger'
    default:
      return 'info'
  }
})

const formatUptime = (seconds: number): string => {
  if (!seconds) return t('pluginManager.txtNA')
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  return t('pluginManager.uptimeFormat', { hours, minutes })
}

const handleConfigChange = async () => {
  if (!props.plugin) return
  try {
    const config = JSON.parse(configJson.value)
    await pluginStore.updatePluginConfig(props.plugin.metadata.id, config)
    ElMessage.success(t('pluginManager.msgConfigUpdated'))
  } catch {
    ElMessage.error(t('pluginManager.msgInvalidJson'))
  }
}
</script>

<style scoped>
.plugin-detail {
  padding: 10px 0;
}
.section {
  margin-top: 20px;
}
.section h4 {
  margin-bottom: 10px;
  color: var(--text-primary);
}
</style>