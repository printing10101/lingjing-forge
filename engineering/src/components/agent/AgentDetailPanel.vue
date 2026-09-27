<template>
  <el-dialog
    :model-value="visible"
    :title="detailTitle"
    width="720px"
    destroy-on-close
    @update:model-value="$emit('update:visible', $event)"
  >
    <div v-loading="agentStore.detailLoading">
      <template v-if="agent">
        <!-- Basic Info -->
        <div class="detail-info">
          <div class="detail-info__row">
            <span class="detail-info__label">Agent ID</span>
            <span>{{ agent.agent_id }}</span>
          </div>
          <div class="detail-info__row">
            <span class="detail-info__label">{{
              t("agentDashboard.labelStatus")
            }}</span>
            <el-tag
              :type="agentStore.statusTagType(agent.status)"
              size="small"
              effect="light"
              round
            >
              {{ agentStore.statusLabel(agent.status) }}
            </el-tag>
          </div>
          <div class="detail-info__row">
            <span class="detail-info__label">{{
              t("agentDashboard.detailCurrentTask")
            }}</span>
            <span>{{ agent.current_task_id || t("agentDashboard.none") }}</span>
          </div>
          <div class="detail-info__row">
            <span class="detail-info__label">{{
              t("agentDashboard.detailLastHeartbeat")
            }}</span>
            <span>{{ agentStore.formatTime(agent.last_heartbeat) }}</span>
          </div>
          <div class="detail-info__row">
            <span class="detail-info__label">{{
              t("agentDashboard.detailCreatedAt")
            }}</span>
            <span>{{ agentStore.formatTime(agent.created_at) }}</span>
          </div>
          <div class="detail-info__row">
            <span class="detail-info__label">{{
              t("agentDashboard.detailUpdatedAt")
            }}</span>
            <span>{{ agentStore.formatTime(agent.updated_at) }}</span>
          </div>
        </div>

        <!-- Session Context -->
        <div v-if="agent.session_context" class="detail-config">
          <h4 class="detail-config__title">
            {{ t("agentDashboard.sectionSessionContext") }}
          </h4>
          <div class="detail-config__row">
            <span class="detail-config__label">{{
              t("agentDashboard.detailTaskDesc")
            }}</span>
            <span>{{
              agent.session_context.task_description || t("agentDashboard.none")
            }}</span>
          </div>
          <div class="detail-config__row">
            <span class="detail-config__label">{{
              t("agentDashboard.detailCurrentStage")
            }}</span>
            <span>{{
              agent.session_context.current_stage || t("agentDashboard.none")
            }}</span>
          </div>
          <div class="detail-config__row">
            <span class="detail-config__label">{{
              t("agentDashboard.detailInjectedSkills")
            }}</span>
            <span>
              <el-tag
                v-for="skill in agent.session_context.injected_skills"
                :key="skill"
                size="small"
                effect="plain"
                style="margin-right: 4px; margin-bottom: 2px"
              >
                {{ skill }}
              </el-tag>
              <span v-if="!agent.session_context.injected_skills?.length">{{
                t("agentDashboard.none")
              }}</span>
            </span>
          </div>
        </div>

        <!-- Checkpoint Info -->
        <div v-if="agent.checkpoint" class="detail-config">
          <h4 class="detail-config__title">
            {{ t("agentDashboard.sectionCheckpoint") }}
          </h4>
          <div class="detail-config__row">
            <span class="detail-config__label">{{
              t("agentDashboard.detailCheckpointId")
            }}</span>
            <span>{{ agent.checkpoint.checkpoint_id }}</span>
          </div>
          <div class="detail-config__row">
            <span class="detail-config__label">Epoch</span>
            <span>{{ agent.checkpoint.epoch }}</span>
          </div>
          <div class="detail-config__row">
            <span class="detail-config__label">Step</span>
            <span>{{ agent.checkpoint.step }}</span>
          </div>
          <div class="detail-config__row">
            <span class="detail-config__label">{{
              t("agentDashboard.detailBestMetric")
            }}</span>
            <span>{{ agent.checkpoint.best_metric ?? "-" }}</span>
          </div>
        </div>

        <!-- Memory Entries -->
        <div v-if="agent.memory?.length" class="detail-config">
          <h4 class="detail-config__title">
            {{
              t("agentDashboard.memoryEntries", { count: agent.memory.length })
            }}
          </h4>
          <div class="detail-logs">
            <div
              v-for="(entry, idx) in agent.memory.slice(0, 10)"
              :key="entry.memory_id || idx"
              class="detail-log-item"
            >
              <div class="detail-log-item__header">
                <span class="detail-log-item__time">{{
                  agentStore.formatTime(entry.created_at)
                }}</span>
                <el-tag size="small" effect="plain">
                  {{ entry.memory_type }}
                </el-tag>
              </div>
              <div class="detail-log-item__text">
                {{
                  entry.content.length > 120
                    ? entry.content.slice(0, 120) + "..."
                    : entry.content
                }}
              </div>
            </div>
          </div>
        </div>

        <!-- No Detail Data Fallback -->
        <div
          v-if="
            !agent.session_context && !agent.checkpoint && !agent.memory?.length
          "
          class="detail-config"
        >
          <p
            style="margin: 0; color: var(--text-tertiary); font-size: 0.875rem"
          >
            {{ t("agentDashboard.noDetailData") }}
          </p>
        </div>
      </template>
    </div>

    <template #footer>
      <el-button
        type="success"
        text
        :disabled="!agent || agent.status === 'stopped'"
        @click="$emit('action', 'resume')"
      >
        {{ t("agentDashboard.btnRestart") }}
      </el-button>
      <el-button
        type="danger"
        :disabled="!agent"
        @click="$emit('action', 'delete')"
      >
        {{ t("agentDashboard.btnDelete") }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { useAgentStore } from "@/stores/agents";
import type { AgentDetail } from "@/stores/agents";

const props = defineProps<{
  visible: boolean;
  agent: AgentDetail | null;
}>();

defineEmits<{
  "update:visible": [value: boolean];
  action: [type: "resume" | "delete"];
}>();

const { t } = useI18n();
const agentStore = useAgentStore();

const detailTitle = computed(() => {
  if (!props.agent) return t("agentDashboard.detailTitle");
  return t("agentDashboard.detailTitleWithId", {
    id: formatAgentId(props.agent.agent_id),
  });
});

function formatAgentId(id: string): string {
  if (!id) return "-";
  return id.charAt(0).toUpperCase() + id.slice(1);
}
</script>

<style scoped>
.detail-info {
  display: flex;
  flex-wrap: wrap;
  gap: 16px 32px;
  margin-bottom: 20px;
}

.detail-info__row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.detail-info__label {
  font-size: 0.8125rem;
  color: var(--text-tertiary);
  flex-shrink: 0;
}

.detail-config {
  background: var(--bg-secondary);
  border-radius: var(--radius-md);
  padding: 16px;
  margin-bottom: 20px;
}

.detail-config__title,
.detail-logs__title {
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 12px;
}

.detail-config__row {
  display: flex;
  justify-content: space-between;
  padding: 6px 0;
  font-size: 0.8125rem;
  border-bottom: 1px solid var(--border-light);
}

.detail-config__row:last-child {
  border-bottom: none;
}

.detail-config__label {
  color: var(--text-tertiary);
  flex-shrink: 0;
  margin-right: 12px;
}

.detail-logs {
  max-height: 240px;
  overflow-y: auto;
}

.detail-log-item {
  padding: 10px 0;
  border-bottom: 1px solid var(--border-light);
}

.detail-log-item:last-child {
  border-bottom: none;
}

.detail-log-item__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}

.detail-log-item__time {
  font-size: 0.75rem;
  color: var(--text-tertiary);
}

.detail-log-item__text {
  font-size: 0.8125rem;
  color: var(--text-secondary);
}
</style>
