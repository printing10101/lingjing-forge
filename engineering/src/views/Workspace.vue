<template>
  <div class="workspace-page">
    <el-card>
      <template #header>
        <div class="header-with-actions">
          <span>{{ $t("workspace.header") }}</span>
          <el-tag type="info" size="small">
            {{ $t("workspace.userSovereignty") }}
          </el-tag>
        </div>
      </template>
      <el-tabs v-model="activeTab">
        <el-tab-pane :label="$t('workspace.predictTab')" name="predict">
          <WorkspacePredictTab />
        </el-tab-pane>

        <!-- W6 物理预演卡：确认前查看 AI 对物理结果的预判 -->
        <el-tab-pane
          :label="$t('workspace.previewTab')"
          name="physical-preview"
        >
          <WorkspacePreviewTab />
        </el-tab-pane>

        <el-tab-pane :label="$t('workspace.trainTab')" name="train">
          <WorkspaceTrainForm
            :train-form="trainForm"
            :dry-running="dryRunning"
            :training="training"
            :train-plan-confirmed="trainPlanConfirmed"
            @dry-run="handleDryRun"
            @train="handleTrain"
            @update:train-form="Object.assign(trainForm, $event)"
          />
          <WorkspaceTrainMonitor
            :dry-run-result="dryRunResult"
            :train-result="trainResult"
            :current-job-id="currentJobId"
            :sse="{
              currentStatus: sse.currentStatus ?? null,
              progress: sse.progress ?? 0,
              lastProgressData: (sse.lastProgressData ?? null) as Record<
                string,
                unknown
              > | null,
              error: sse.error ?? null,
            }"
            :loss-history="lossHistory"
            :val-loss-history="valLossHistory"
            :cancelling="cancelling"
            :train-plan-confirmed="trainPlanConfirmed"
            @cancel-training="handleCancelTraining"
            @update:train-plan-confirmed="trainPlanConfirmed = $event"
          />
        </el-tab-pane>

        <el-tab-pane :label="$t('workspace.modelsTab')" name="models">
          <WorkspaceModelsTab />
        </el-tab-pane>

        <el-tab-pane :label="$t('workspace.pluginsTab')" name="plugins">
          <!-- 扩展点面板宿主：渲染插件向 workspace.panel 注册的面板（如方言管理） -->
          <WorkspacePanelHost
            layout="tabs"
            :empty-text="$t('workspace.pluginsEmpty')"
          />
        </el-tab-pane>
      </el-tabs>
    </el-card>
  </div>
</template>

<script setup lang="ts">
// 注：本文件已从 1087 行拆至 379 行——训练表单/监控/预测/模型列表
// 已分别抽为 WorkspaceTrainForm / WorkspaceTrainMonitor / WorkspacePredictTab /
// WorkspaceModelsTab，插件面板由 WorkspacePanelHost 渲染。剩余编排逻辑保持内联。
import { ref, reactive, onMounted, computed } from "vue";
import { useI18n } from "vue-i18n";
import { ElMessage, ElMessageBox } from "element-plus";
import http from "@/utils/http";
import WorkspacePredictTab from "@/components/workspace/WorkspacePredictTab.vue";
import WorkspacePreviewTab from "@/components/workspace/WorkspacePreviewTab.vue";
import WorkspaceModelsTab from "@/components/workspace/WorkspaceModelsTab.vue";
import WorkspaceTrainForm from "@/components/workspace/WorkspaceTrainForm.vue";
import WorkspaceTrainMonitor from "@/components/workspace/WorkspaceTrainMonitor.vue";
import WorkspacePanelHost from "@/components/WorkspacePanelHost.vue";
import { useEventSource } from "@/composables/useEventSource";
import { API_CONFIG, buildApiPath } from "@/config/api";

const { t } = useI18n();
const activeTab = ref("predict");
const training = ref(false);
const dryRunning = ref(false);
const trainPlanConfirmed = ref(false);

interface DryRunResult {
  is_dry_run: boolean;
  training_plan: {
    estimated_duration_minutes: number;
    estimated_memory_mb: number;
    estimated_gpu_memory_mb?: number;
    dataset_samples: number;
    train_val_split: { train: number; validation: number; ratio: string };
    potential_risks: string[];
    recommendations: string[];
  };
  confidence: number;
  reasoning: string;
}

interface TrainResult {
  job_id: string;
  status: string;
  message?: string;
}

interface ModelInfo {
  name: string;
  model_type: string;
  version: string;
  input_features?: string[];
}

const trainForm = reactive({
  modelName: "",
  dataPath: "",
  hyperparameters: {
    learning_rate: 0.001,
    epochs: 100,
    batch_size: 32,
    optimizer: "adam",
  },
  device: "auto",
});

const dryRunResult = ref<DryRunResult | null>(null);
const trainResult = ref<TrainResult | null>(null);
const modelList = ref<ModelInfo[]>([]);

const currentJobId = ref<string | null>(null);
const sseJobId = ref("");
const sse = reactive(
  useEventSource(sseJobId, { autoReconnect: true, maxRetries: 10 }),
);
const cancelling = ref(false);

function connectToJob(jobId: string) {
  sseJobId.value = jobId;
  sse.reset();
  sse.connect();
}

const lossHistory = computed(() => {
  const losses: number[] = [];
  if (!sse.events) return losses;
  for (const event of sse.events) {
    if (
      event.type === "progress" &&
      event.data.metrics?.train_loss !== undefined
    ) {
      losses.push(event.data.metrics.train_loss as number);
    }
  }
  return losses;
});

const valLossHistory = computed(() => {
  const losses: number[] = [];
  if (!sse.events) return losses;
  for (const event of sse.events) {
    if (
      event.type === "progress" &&
      event.data.metrics?.val_loss !== undefined
    ) {
      losses.push(event.data.metrics.val_loss as number);
    }
  }
  return losses;
});

async function handleDryRun() {
  if (!trainForm.modelName || !trainForm.dataPath) {
    ElMessage.warning(t("common.inputPlaceholder"));
    return;
  }

  dryRunning.value = true;
  dryRunResult.value = null;
  trainPlanConfirmed.value = false;

  try {
    const res = await http.post(
      buildApiPath(API_CONFIG.LNN, "/train/dry_run"),
      {
        model_name: trainForm.modelName,
        data_path: trainForm.dataPath,
        hyperparameters: trainForm.hyperparameters,
        device: trainForm.device,
      },
    );

    dryRunResult.value = res.data.data as DryRunResult;
    ElMessage.success(t("workspace.trainingPlanSummary"));
  } catch (e: unknown) {
    const errorMsg = e instanceof Error ? e.message : String(e);
    ElMessage.error(errorMsg || t("common.unknownError"));
  } finally {
    dryRunning.value = false;
  }
}

async function handleTrain() {
  if (!trainPlanConfirmed.value) {
    ElMessage.warning(t("workspace.confirmTraining"));
    return;
  }

  training.value = true;
  trainResult.value = null;

  try {
    const res = await http.post(buildApiPath(API_CONFIG.LNN, "/train"), {
      model_name: trainForm.modelName,
      data_path: trainForm.dataPath,
      hyperparameters: trainForm.hyperparameters,
      device: trainForm.device,
    });

    const jobId = res.data.data?.job_id;
    if (!jobId) {
      ElMessage.error(t("workspace.jobId"));
      return;
    }

    currentJobId.value = jobId;
    connectToJob(jobId);

    trainResult.value = res.data.data;
    ElMessage.success(t("workspace.trainingMonitor"));

    await recordAuditLog(
      "lnn_train",
      dryRunResult.value,
      "accept",
      "success",
      trainForm,
    );
  } catch (e: unknown) {
    const errorMsg = e instanceof Error ? e.message : String(e);
    ElMessage.error(errorMsg || t("common.unknownError"));
    await recordAuditLog(
      "lnn_train",
      dryRunResult.value,
      "reject",
      "failed",
      trainForm,
    );
  } finally {
    training.value = false;
  }
}

async function handleCancelTraining() {
  if (!currentJobId.value) return;

  try {
    await ElMessageBox.confirm(
      t("workspace.confirmCancelTraining"),
      t("workspace.confirmCancelTitle"),
      {
        confirmButtonText: t("common.confirm"),
        cancelButtonText: t("common.cancel"),
        type: "warning",
      },
    );

    cancelling.value = true;
    await http.post(
      buildApiPath(API_CONFIG.JOBS, `/${currentJobId.value}/cancel`),
    );
    ElMessage.info(t("workspace.trainingCancelled"));
  } catch (e: unknown) {
    if (e !== "cancel") {
      ElMessage.error(t("common.failed"));
    }
  } finally {
    cancelling.value = false;
  }
}

async function recordAuditLog(
  aiModule: string,
  aiRecommendation: Record<string, unknown> | null,
  userDecision: string,
  operationStatus: string,
  finalExecution?: Record<string, unknown>,
) {
  try {
    await http.post(
      buildApiPath(API_CONFIG.USER_SOVEREIGNTY, "/audit-log/record"),
      null,
      {
        params: {
          ai_module: aiModule,
          ai_recommendation: JSON.stringify(aiRecommendation || {}),
          user_decision: userDecision,
          final_execution: JSON.stringify(finalExecution || {}),
          operation_status: operationStatus,
          confidence: dryRunResult.value?.confidence || null,
          reasoning: dryRunResult.value?.reasoning || null,
        },
      },
    );
  } catch (e: unknown) {
    // 审计日志记录失败不应阻塞用户主流程，但需记录便于后续审计追溯
    console.warn("[Workspace] recordAuditLog failed:", e);
  }
}

onMounted(async () => {
  try {
    const res = await http.get(buildApiPath(API_CONFIG.LNN, "/models"));
    modelList.value = res.data?.data?.models || [];
  } catch {
    modelList.value = [];
  }
});
</script>

<style scoped>
.workspace-page {
  max-width: 1200px;
  margin: 0 auto;
}

.header-with-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.result-section {
  background: var(--bg-secondary);
  border-radius: var(--radius-md);
  padding: 16px;
  border: 1px solid var(--border-light);
}

.result-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.prediction-value {
  margin: 16px 0;
  padding: 12px;
  background: var(--bg-card);
  border-radius: var(--radius-sm);
  border-left: 4px solid var(--accent-primary);
}

.prediction-value .label {
  font-weight: 600;
  color: var(--text-secondary);
  margin-right: 8px;
}

.prediction-value .value {
  font-size: 18px;
  font-weight: 700;
  color: var(--text-primary);
}

.reasoning-section {
  margin: 16px 0;
  padding: 12px;
  background: var(--bg-card);
  border-radius: var(--radius-sm);
  border-left: 4px solid var(--success);
}

.reasoning-section h5 {
  margin: 0 0 8px 0;
  color: var(--success);
  font-weight: 600;
}

.reasoning-section p {
  margin: 0;
  color: var(--text-secondary);
  line-height: 1.6;
}

.adjusted-result {
  margin-top: 16px;
  padding: 12px;
  background: var(--bg-card);
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-light);
}

.adjusted-result pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 12px;
  color: var(--text-secondary);
}
</style>
