<template>
  <div class="rl-agent-page">
    <RlAgentHeader :loading="store.anyLoading" @refresh="handleRefresh" />

    <div class="rl-main">
      <RlAgentVersionList @selectVersion="handleSelectVersion" />

      <section class="rl-detail-panel">
        <RlAgentVersionDetail @useActiveVersion="handleUseActiveVersion" />
        <RlAgentActionCard />
        <RlAgentTrainingCard />
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { useRlAgentStore } from '@/stores/rlAgent'
import RlAgentHeader from '@/components/rl_agent/RlAgentHeader.vue'
import RlAgentVersionList from '@/components/rl_agent/RlAgentVersionList.vue'
import RlAgentVersionDetail from '@/components/rl_agent/RlAgentVersionDetail.vue'
import RlAgentActionCard from '@/components/rl_agent/RlAgentActionCard.vue'
import RlAgentTrainingCard from '@/components/rl_agent/RlAgentTrainingCard.vue'

const { t } = useI18n()
const store = useRlAgentStore()

async function loadVersions(): Promise<void> {
  const result = await store.fetchVersions({ limit: 50, offset: 0 })
  if (!result) {
    ElMessage.error(store.error || t('rlAgent.loadFailed'))
  }
}

async function handleSelectVersion(version: string): Promise<void> {
  const result = await store.fetchVersion(version)
  if (!result) {
    ElMessage.error(store.error || t('rlAgent.loadFailed'))
  }
}

function handleUseActiveVersion(): void {
  ElMessage.success(t('rlAgent.modelUriFilled'))
}

async function handleRefresh(): Promise<void> {
  await loadVersions()
  try { await store.fetchTrainingStatus() } catch { /* polling 覆盖 */ }
}

onMounted(() => {
  void loadVersions().catch((e: unknown) => {
    console.warn('[RLAgent] loadVersions failed:', e)
  })
  void store.fetchTrainingStatus()
})
</script>

<style scoped>
.rl-agent-page {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.rl-main {
  display: grid;
  grid-template-columns: 360px 1fr;
  gap: 16px;
  align-items: start;
}

.rl-detail-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
</style>
