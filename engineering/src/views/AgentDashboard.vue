<template>
  <div class="agent-dashboard">
    <!-- ==================== 1. Filter Bar + Deploy Button ==================== -->
    <AgentFilterBar
      :status-filter="statusFilter"
      @update:status-filter="handleFilterChange"
      @deploy="deployDialogVisible = true"
    />

    <!-- ==================== 2. Summary Stats Bar ==================== -->
    <div class="stats-row">
      <div class="stat-card">
        <div class="stat-card__content">
          <span class="stat-card__label">{{
            t("agentDashboard.statTotal")
          }}</span>
          <span class="stat-card__value">{{ stats.total }}</span>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-card__content">
          <span class="stat-card__label">{{
            t("agentDashboard.statActive")
          }}</span>
          <span class="stat-card__value" style="color: var(--success)">{{
            stats.active
          }}</span>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-card__content">
          <span class="stat-card__label">{{
            t("agentDashboard.statIdle")
          }}</span>
          <span class="stat-card__value">{{ stats.idle }}</span>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-card__content">
          <span class="stat-card__label">{{
            t("agentDashboard.statError")
          }}</span>
          <span class="stat-card__value" style="color: var(--error)">{{
            stats.error
          }}</span>
        </div>
      </div>
    </div>

    <!-- ==================== 3. Agent Card Grid ==================== -->
    <div v-loading="agentStore.loading" class="agent-grid">
      <AgentCard
        v-for="agent in filteredAgents"
        :key="agent.agent_id"
        :agent="agent"
        @action="handleCardAction"
      />

      <!-- Empty State -->
      <div
        v-if="filteredAgents.length === 0 && !agentStore.loading"
        class="agent-grid__empty"
      >
        <el-empty
          :description="
            dataLoadError
              ? t('agentDashboard.emptyLoadFailed')
              : t('agentDashboard.emptyNoData')
          "
        />
      </div>
    </div>

    <!-- ==================== 4. Activity Timeline ==================== -->
    <div class="activity-section">
      <h2 class="section-title">
        {{ t("agentDashboard.sectionActivity") }}
      </h2>
      <div
        v-if="agentStore.activityLoading"
        v-loading="true"
        class="activity-loading"
      />
      <el-empty
        v-else-if="agentStore.activityError"
        :description="t('agentDashboard.activityLoadFailed')"
        :image-size="72"
      />
      <el-empty
        v-else-if="agentStore.activityEntries.length === 0"
        :description="t('agentDashboard.activityEmpty')"
        :image-size="72"
      />
      <el-timeline v-else class="activity-timeline">
        <el-timeline-item
          v-for="entry in agentStore.activityEntries"
          :key="`${entry.timestamp_ms}-${entry.route}-${entry.agent_id}`"
          :timestamp="formatActivityTime(entry.timestamp_ms)"
          :type="entry.status_code >= 400 ? 'danger' : 'success'"
          :hollow="entry.status_code < 400"
        >
          <span class="activity-agent">{{
            entry.agent_id || "anonymous"
          }}</span>
          <span class="activity-route">{{ entry.route }}</span>
          <span class="activity-meta"
            >{{ entry.status_code }} · {{ entry.latency_ms.toFixed(1) }}ms</span
          >
        </el-timeline-item>
      </el-timeline>
    </div>

    <!-- ==================== 5. Deploy New Agent Dialog ==================== -->
    <AgentDeployDialog
      :visible="deployDialogVisible"
      :loading="deployLoading"
      @update:visible="deployDialogVisible = $event"
      @submit="handleDeploy"
    />

    <!-- ==================== 6. Agent Detail Panel ==================== -->
    <AgentDetailPanel
      :visible="detailDialogVisible"
      :agent="agentStore.currentAgent"
      @update:visible="detailDialogVisible = $event"
      @action="handleDetailAction"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useI18n } from "vue-i18n";
import { useAgentStore } from "@/stores/agents";
import type { AgentSummary } from "@/stores/agents";

import AgentFilterBar from "@/components/agent/AgentFilterBar.vue";
import AgentCard from "@/components/agent/AgentCard.vue";
import AgentDeployDialog from "@/components/agent/AgentDeployDialog.vue";
import AgentDetailPanel from "@/components/agent/AgentDetailPanel.vue";

/* ------------------------------------------------------------------ */
/*  i18n                                                               */
/* ------------------------------------------------------------------ */
const { t } = useI18n();

/* ------------------------------------------------------------------ */
/*  Store                                                              */
/* ------------------------------------------------------------------ */
const agentStore = useAgentStore();

/* ------------------------------------------------------------------ */
/*  Data Load Error State                                              */
/* ------------------------------------------------------------------ */
const dataLoadError = ref(false);

/* ------------------------------------------------------------------ */
/*  Lifecycle                                                          */
/* ------------------------------------------------------------------ */
onMounted(async () => {
  try {
    await agentStore.fetchAgents();
  } catch {
    dataLoadError.value = true;
  }
  // 活动日志：真实数据来自 Agent Gateway 审计日志（需管理员权限，
  // 失败时由 activityError 驱动诚实空态，不阻塞主数据加载）
  agentStore.fetchActivity();
});

/**
 * 活动时间：审计日志 timestamp_ms 为毫秒时间戳
 * （注意与 formatDateTimeSafe 的秒级语义区分）
 */
function formatActivityTime(tsMs: number): string {
  if (!tsMs) return "-";
  const d = new Date(tsMs);
  return Number.isNaN(d.getTime()) ? "-" : d.toLocaleString("zh-CN");
}

/* ------------------------------------------------------------------ */
/*  Computed: Display Agents & Stats                                   */
/* ------------------------------------------------------------------ */
const displayAgents = computed<AgentSummary[]>(() => agentStore.agents);

const stats = computed(() => {
  const agents = agentStore.agents;
  const activeCount = agents.filter((a) => a.status === "busy").length;
  const idleCount = agents.filter((a) => a.status === "idle").length;
  const errorCount = agents.filter((a) => a.status === "error").length;
  return {
    total: agents.length,
    active: activeCount,
    idle: idleCount,
    error: errorCount,
  };
});

/* ------------------------------------------------------------------ */
/*  Filter                                                             */
/* ------------------------------------------------------------------ */
const statusFilter = ref<string>("all");

const filteredAgents = computed(() => {
  if (statusFilter.value === "all") return displayAgents.value;
  return displayAgents.value.filter((a) => a.status === statusFilter.value);
});

function handleFilterChange(val: string) {
  if (agentStore.agents.length > 0) {
    agentStore.statusFilter = val === "all" ? null : val;
    agentStore.fetchAgents();
  }
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */
function formatAgentId(id: string): string {
  if (!id) return "-";
  return id.charAt(0).toUpperCase() + id.slice(1);
}

/* ------------------------------------------------------------------ */
/*  Deploy Dialog                                                       */
/* ------------------------------------------------------------------ */
const deployDialogVisible = ref(false);
const deployLoading = ref(false);

async function handleDeploy(payload: { name: string; type: string }) {
  deployLoading.value = true;
  try {
    const agentId = payload.name.toLowerCase().replace(/\s+/g, "-");
    await agentStore.deployAgent(agentId, {
      name: payload.name,
      type: payload.type,
    });
    ElMessage.success(
      t("agentDashboard.msgDeploySuccess", { name: payload.name }),
    );
    deployDialogVisible.value = false;
    agentStore.fetchAgents();
  } catch (e: unknown) {
    console.warn("[AgentDashboard] deploy failed:", e);
    ElMessage.error(t("agentDashboard.msgDeployFailed"));
  } finally {
    deployLoading.value = false;
  }
}

/* ------------------------------------------------------------------ */
/*  Detail Dialog                                                       */
/* ------------------------------------------------------------------ */
const detailDialogVisible = ref(false);

async function handleShowDetail(agent: AgentSummary) {
  detailDialogVisible.value = true;
  try {
    await agentStore.fetchAgentDetail(agent.agent_id);
  } catch {
    ElMessage.error(t("agentDashboard.msgGetDetailFailed"));
  }
}

/* ------------------------------------------------------------------ */
/*  Card Actions                                                        */
/* ------------------------------------------------------------------ */
async function handleCardAction(payload: {
  type: "detail" | "resume" | "delete";
  agent: AgentSummary;
}) {
  if (payload.type === "detail") {
    await handleShowDetail(payload.agent);
  } else if (payload.type === "resume") {
    await handleResume(payload.agent);
  } else if (payload.type === "delete") {
    await handleDelete(payload.agent);
  }
}

async function handleResume(agent: AgentSummary) {
  try {
    await agentStore.resumeAgent(agent.agent_id);
    ElMessage.success(
      t("agentDashboard.msgRestartSuccess", {
        id: formatAgentId(agent.agent_id),
      }),
    );
    await agentStore.fetchAgents();
  } catch {
    ElMessage.error(t("agentDashboard.msgRestartFailed"));
  }
}

async function handleDelete(agent: AgentSummary) {
  try {
    await ElMessageBox.confirm(
      t("agentDashboard.msgDeleteConfirm", {
        id: formatAgentId(agent.agent_id),
      }),
      t("agentDashboard.msgDeleteConfirmTitle"),
      {
        confirmButtonText: t("agentDashboard.btnConfirmDelete"),
        cancelButtonText: t("agentDashboard.btnCancel"),
        type: "warning",
      },
    );
    await agentStore.deleteAgent(agent.agent_id);
    ElMessage.success(
      t("agentDashboard.msgDeleteSuccess", {
        id: formatAgentId(agent.agent_id),
      }),
    );
  } catch {
    // 用户取消
  }
}

/* ------------------------------------------------------------------ */
/*  Detail Dialog Actions                                               */
/* ------------------------------------------------------------------ */
function handleDetailAction(type: "resume" | "delete") {
  if (type === "resume" && agentStore.currentAgent) {
    handleResume(agentStore.currentAgent);
  } else if (type === "delete" && agentStore.currentAgent) {
    handleDeleteFromDetail();
  }
}

async function handleDeleteFromDetail() {
  if (!agentStore.currentAgent) return;
  try {
    await ElMessageBox.confirm(
      t("agentDashboard.msgDeleteConfirm", {
        id: formatAgentId(agentStore.currentAgent.agent_id),
      }),
      t("agentDashboard.msgDeleteConfirmTitle"),
      {
        confirmButtonText: t("agentDashboard.btnConfirmDelete"),
        cancelButtonText: t("agentDashboard.btnCancel"),
        type: "warning",
      },
    );
    await agentStore.deleteAgent(agentStore.currentAgent.agent_id);
    detailDialogVisible.value = false;
    ElMessage.success(
      t("agentDashboard.msgDeleteSuccess", {
        id: formatAgentId(agentStore.currentAgent.agent_id),
      }),
    );
  } catch {
    // 用户取消
  }
}
</script>

<style scoped>
/* ================================================================ */
/*  Page-level layout                                                  */
/* ================================================================ */
.agent-dashboard {
  max-width: var(--content-max-width);
  margin: 0 auto;
  padding: var(--page-padding);
}

/* ================================================================ */
/*  Stats Row                                                          */
/* ================================================================ */
.stats-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}

.stat-card {
  background: var(--bg-primary);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
  padding: 16px 20px;
  transition: box-shadow var(--transition-normal);
}

.stat-card:hover {
  box-shadow: var(--shadow-sm);
}

.stat-card__content {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.stat-card__label {
  font-size: 0.75rem;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.stat-card__value {
  font-size: 1.75rem;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1.2;
}

/* ================================================================ */
/*  Agent Card Grid                                                   */
/* ================================================================ */
.agent-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-bottom: 32px;
}

.agent-grid__empty {
  grid-column: 1 / -1;
  display: flex;
  justify-content: center;
  padding: 60px 0;
}

/* ================================================================ */
/*  Activity Timeline                                                  */
/* ================================================================ */
.section-title {
  font-size: 1.125rem;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 16px;
}

.activity-section {
  margin-bottom: 24px;
}

.activity-loading {
  min-height: 96px;
}

.activity-timeline {
  padding-left: 4px;
  margin: 0;
}

.activity-agent {
  font-weight: 600;
  margin-right: 8px;
  color: var(--text-primary);
}

.activity-route {
  font-family: monospace;
  margin-right: 8px;
  color: var(--text-secondary, #909399);
}

.activity-meta {
  font-size: 12px;
  color: var(--text-secondary, #909399);
}

/* ================================================================ */
/*  Responsive                                                         */
/* ================================================================ */
@media (max-width: 1024px) {
  .agent-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .stats-row {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 640px) {
  .agent-grid {
    grid-template-columns: 1fr;
  }
  .stats-row {
    grid-template-columns: 1fr 1fr;
  }
}
</style>
