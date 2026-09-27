<script setup lang="ts">
// 原 /template-detail/:id 独立页，现改为模板中心内的详情抽屉。
// 分支信息 + 演化时间线 + A/B 实验 + 指标，数据来自 4 个并行请求。
import { ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { formatSecondsTimestamp } from "@/utils/formatters";
import { getBranchTypeTagType } from "@/utils/statusHelpers";
import http from "@/utils/http";
import { API_CONFIG, buildApiPath } from "@/config/api";

const { t } = useI18n();

const props = defineProps<{
  visible: boolean;
  branchId: string | null;
}>();

const emit = defineEmits<{
  "update:visible": [value: boolean];
}>();

// 类型定义
interface Branch {
  id: string;
  name: string;
  type: string;
  base_branch: string | null;
  data: Record<string, unknown>;
  metadata: Record<string, unknown>;
  template_data?: Record<string, unknown>;
  commit_log?: Array<Record<string, unknown>>;
  created_at: number;
  updated_at: number;
}

interface EvolutionEvent {
  event_id: string;
  id: string;
  branch_id: string;
  event_type: string;
  description: string;
  action: string;
  suggestion_id: string;
  details: Record<string, unknown>;
  timestamp: number;
  created_at: number;
  metadata: Record<string, unknown>;
}

interface ABExperiment {
  experiment_id: string;
  name: string;
  control_branch: string;
  candidate_branch: string;
  status: string;
  start_time: number;
  end_time?: number;
  results?: Record<string, unknown>;
}

interface TemplateMetrics {
  downloads: number;
  subscriptions: number;
  rating: number;
  usage_count: number;
  success_rate: number;
  total_experiments: number;
  adoption_count: number;
  last_updated: number;
}

const branch = ref<Branch | null>(null);
const evolutionHistory = ref<EvolutionEvent[]>([]);
const abExperiments = ref<ABExperiment[]>([]);
const metrics = ref<TemplateMetrics | null>(null);
const loading = ref(false);
const activeTab = ref("info");

watch(
  () => [props.visible, props.branchId] as const,
  ([visible, id]) => {
    if (visible && id) {
      fetchAll(id);
    } else if (!visible) {
      // 关闭时清空，避免下次打开闪现旧数据
      branch.value = null;
      evolutionHistory.value = [];
      abExperiments.value = [];
      metrics.value = null;
      activeTab.value = "info";
    }
  },
);

async function fetchAll(id: string) {
  loading.value = true;
  try {
    // 并行请求 4 个独立接口，减少总等待时间；单个失败不影响其他
    await Promise.allSettled([
      fetchBranch(id),
      fetchEvolutionHistory(id),
      fetchABExperiments(id),
      fetchMetrics(id),
    ]);
  } finally {
    loading.value = false;
  }
}

async function fetchBranch(id: string) {
  const res = await http.get(
    buildApiPath(API_CONFIG.V1, `/templates/branches/${id}`),
  );
  if (res.data.code === "SUCCESS") branch.value = res.data.data;
}

async function fetchEvolutionHistory(id: string) {
  const res = await http.get(
    buildApiPath(API_CONFIG.V1, "/templates/evolution/history"),
    { params: { branch_id: id } },
  );
  if (res.data.code === "SUCCESS") evolutionHistory.value = res.data.data;
}

async function fetchABExperiments(id: string) {
  const res = await http.get(
    buildApiPath(API_CONFIG.V1, "/templates/ab_tests"),
  );
  if (res.data.code === "SUCCESS") {
    abExperiments.value = (res.data.data || []).filter(
      (e: ABExperiment) => e.control_branch === id || e.candidate_branch === id,
    );
  }
}

async function fetchMetrics(id: string) {
  const res = await http.get(
    buildApiPath(API_CONFIG.V1, `/template_market/templates/${id}/metrics`),
  );
  if (res.data.code === "SUCCESS") metrics.value = res.data.data;
}
</script>

<template>
  <el-drawer
    :model-value="visible"
    size="620px"
    :title="branch?.name || t('templateDetail.title')"
    @update:model-value="emit('update:visible', $event)"
  >
    <div v-loading="loading" class="detail-body">
      <template v-if="branch">
        <el-card class="info-card">
          <template #header>
            <div class="branch-header">
              <span class="branch-name">{{ branch.name }}</span>
              <el-tag
                :type="getBranchTypeTagType(branch.metadata?.type as string)"
              >
                {{ (branch.metadata?.type as string) || "unknown" }}
              </el-tag>
            </div>
          </template>

          <el-descriptions :column="1" border>
            <el-descriptions-item :label="t('templateDetail.branchId')">
              {{ branch.id }}
            </el-descriptions-item>
            <el-descriptions-item :label="t('templateDetail.baseBranch')">
              {{ branch.base_branch || t("templateDetail.none") }}
            </el-descriptions-item>
            <el-descriptions-item :label="t('templateDetail.createdAt')">
              {{ formatSecondsTimestamp(branch.created_at) }}
            </el-descriptions-item>
            <el-descriptions-item :label="t('templateDetail.updatedAt')">
              {{ formatSecondsTimestamp(branch.updated_at) }}
            </el-descriptions-item>
            <el-descriptions-item :label="t('templateDetail.commits')">
              {{
                t("templateDetail.commitCount", {
                  count: branch.commit_log?.length || 0,
                })
              }}
            </el-descriptions-item>
          </el-descriptions>
        </el-card>

        <el-tabs v-model="activeTab" class="detail-tabs">
          <el-tab-pane :label="t('templateDetail.tabTemplate')" name="info">
            <pre class="template-data">{{
              JSON.stringify(branch.template_data, null, 2)
            }}</pre>
          </el-tab-pane>

          <el-tab-pane
            :label="t('templateDetail.tabEvolution')"
            name="evolution"
          >
            <el-timeline v-if="evolutionHistory.length > 0">
              <el-timeline-item
                v-for="entry in evolutionHistory"
                :key="entry.id"
                :timestamp="formatSecondsTimestamp(entry.created_at)"
              >
                <el-tag
                  :type="entry.action === 'applied' ? 'success' : 'info'"
                  size="small"
                >
                  {{ entry.action }}
                </el-tag>
                <div class="evolution-detail">
                  {{
                    t("templateDetail.suggestionId", {
                      id: entry.suggestion_id,
                    })
                  }}
                  <pre v-if="entry.details">{{
                    JSON.stringify(entry.details, null, 2)
                  }}</pre>
                </div>
              </el-timeline-item>
            </el-timeline>
            <el-empty v-else :description="t('templateDetail.noEvolution')" />
          </el-tab-pane>

          <el-tab-pane :label="t('templateDetail.tabAb')" name="ab">
            <el-table :data="abExperiments" stripe>
              <el-table-column
                prop="name"
                :label="t('templateDetail.experimentName')"
              />
              <el-table-column
                :label="t('templateDetail.controlGroup')"
                width="120"
              >
                <template #default="{ row }">
                  <el-tag>{{
                    row.control_branch === branchId
                      ? t("templateDetail.thisBranch")
                      : row.control_branch
                  }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column
                :label="t('templateDetail.candidateGroup')"
                width="120"
              >
                <template #default="{ row }">
                  <el-tag
                    :type="
                      row.candidate_branch === branchId ? 'primary' : 'info'
                    "
                  >
                    {{
                      row.candidate_branch === branchId
                        ? t("templateDetail.thisBranch")
                        : row.candidate_branch
                    }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column
                prop="status"
                :label="t('userManagement.colStatus')"
                width="100"
              />
              <el-table-column
                prop="result"
                :label="t('templateDetail.result')"
                width="120"
              />
            </el-table>
            <el-empty
              v-if="abExperiments.length === 0"
              :description="t('templateDetail.noAbTests')"
            />
          </el-tab-pane>

          <el-tab-pane :label="t('templateDetail.tabMetrics')" name="metrics">
            <el-descriptions v-if="metrics" :column="1" border>
              <el-descriptions-item :label="t('templateDetail.successRate')">
                {{ (metrics.success_rate * 100).toFixed(1) }}%
              </el-descriptions-item>
              <el-descriptions-item
                :label="t('templateDetail.totalExperiments')"
              >
                {{ metrics.total_experiments }}
              </el-descriptions-item>
              <el-descriptions-item :label="t('templateDetail.adoptionCount')">
                {{ metrics.adoption_count }}
              </el-descriptions-item>
              <el-descriptions-item :label="t('templateDetail.lastUpdated')">
                {{ formatSecondsTimestamp(metrics.last_updated) }}
              </el-descriptions-item>
            </el-descriptions>
            <el-empty v-else :description="t('templateDetail.noMetrics')" />
          </el-tab-pane>
        </el-tabs>
      </template>
      <el-empty
        v-else-if="!loading"
        :description="t('templateDetail.noMetrics')"
      />
    </div>
  </el-drawer>
</template>

<style scoped>
.branch-header {
  display: flex;
  align-items: center;
  gap: 12px;
}
.branch-name {
  font-weight: 600;
  font-size: 15px;
}
.info-card {
  margin-bottom: 12px;
}
.detail-tabs {
  margin-top: 4px;
}
.template-data {
  max-height: 400px;
  overflow: auto;
  font-family: var(--font-mono);
  font-size: 13px;
  background: var(--bg-secondary);
  padding: 12px;
  border-radius: var(--radius-sm);
}
.evolution-detail {
  margin-top: 4px;
  font-size: 13px;
}
.evolution-detail pre {
  max-height: 200px;
  overflow: auto;
  background: var(--bg-secondary);
  padding: 8px;
  border-radius: var(--radius-sm);
  margin-top: 4px;
}
</style>
