<template>
  <el-card v-loading="store.versionLoading" class="rl-detail-card">
    <template #header>
      <div class="rl-detail-card__header">
        <span>{{ t('rlAgent.versionDetail') }}</span>
        <el-button
          v-if="store.currentVersion"
          link
          type="primary"
          @click="$emit('useActiveVersion')"
        >
          {{ t('rlAgent.useForAction') }}
        </el-button>
      </div>
    </template>
    <el-empty
      v-if="!store.versionLoading && !store.currentVersion"
      :description="t('rlAgent.selectVersionHint')"
    />
    <el-descriptions v-else-if="store.currentVersion" :column="2" border>
      <el-descriptions-item :label="t('rlAgent.fields.version')">
        {{ store.currentVersion.version }}
      </el-descriptions-item>
      <el-descriptions-item :label="t('rlAgent.fields.modelUri')">
        {{ store.currentVersion.model_uri }}
      </el-descriptions-item>
      <el-descriptions-item :label="t('rlAgent.fields.algorithm')">
        <el-tag size="small" :type="POLICY_ALGORITHM_TAG_TYPE[store.currentVersion.algorithm]">
          {{ POLICY_ALGORITHM_LABELS[store.currentVersion.algorithm] }}
        </el-tag>
      </el-descriptions-item>
      <el-descriptions-item :label="t('rlAgent.fields.trainingEpisodes')">
        {{ store.currentVersion.training_episodes }}
      </el-descriptions-item>
      <el-descriptions-item :label="t('rlAgent.fields.trainingSteps')">
        {{ store.currentVersion.training_steps }}
      </el-descriptions-item>
      <el-descriptions-item :label="t('rlAgent.fields.meanReward')">
        {{ store.currentVersion.mean_reward.toFixed(4) }}
      </el-descriptions-item>
      <el-descriptions-item :label="t('rlAgent.fields.createdAt')">
        {{ formatDateTime(store.currentVersion.created_at) }}
      </el-descriptions-item>
      <el-descriptions-item :label="t('rlAgent.fields.isActive')">
        <el-tag :type="store.currentVersion.is_active ? 'success' : 'info'" size="small">
          {{ store.currentVersion.is_active ? t('common.yes') : t('common.no') }}
        </el-tag>
      </el-descriptions-item>
    </el-descriptions>
  </el-card>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useRlAgentStore } from '@/stores/rlAgent'
import {
  POLICY_ALGORITHM_LABELS,
  POLICY_ALGORITHM_TAG_TYPE,
} from '@/contracts/rl_agent'
import { formatDateTime } from '@/utils/dateTime'

const { t } = useI18n()
const store = useRlAgentStore()

defineEmits<{ useActiveVersion: [] }>()
</script>

<style scoped>
.rl-detail-card__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
