<template>
  <div class="nl-modeling-page">
    <div class="page-header">
      <div class="page-header__title">
        <h1>{{ t("nlModeling.pageTitle") }}</h1>
        <p class="subtitle">
          {{ t("nlModeling.pageSubtitle") }}
        </p>
      </div>
      <div class="page-header__actions">
        <el-button @click="toggleViewMode">
          <el-icon><Switch /></el-icon>
          {{
            viewMode === "guide"
              ? t("nlModeling.switchToChat")
              : t("nlModeling.switchToGuide")
          }}
        </el-button>
      </div>
    </div>

    <!-- 工作流向导模式 -->
    <div v-if="viewMode === 'guide'" class="guide-mode">
      <WorkflowGuide
        @step-change="handleStepChange"
        @params-extracted="handleParamsExtracted"
        @generate-model="handleGenerateModel"
        @generate-process="handleGenerateProcess"
        @generate-nc="handleGenerateNC"
        @start-simulation="handleStartSimulation"
        @complete="handleComplete"
      >
        <template #3d-viewer>
          <SimulationViewer
            v-if="currentModelPath"
            :model-path="currentModelPath"
            :show-grid="true"
            :show-axes="true"
            height="100%"
          />
        </template>
        <template #simulation-viewer>
          <SimulationViewer
            v-if="currentModelPath"
            :model-path="currentModelPath"
            :show-grid="true"
            :show-axes="true"
            height="100%"
          />
        </template>
      </WorkflowGuide>
    </div>

    <!-- 对话模式 -->
    <div v-else class="chat-mode">
      <div class="modeling-content">
        <!-- 左侧：NL输入面板 -->
        <div class="input-panel">
          <NLInputPanel
            @model-generated="handleModelGenerated"
            @view-3d="handleView3D"
          />
        </div>

        <!-- 右侧：3D预览 -->
        <div class="preview-panel">
          <div class="preview-header">
            <h3>{{ t("nlModeling.previewTitle") }}</h3>
            <div class="preview-actions">
              <el-button size="small" @click="handleResetView">
                <el-icon><Refresh /></el-icon>{{ t("nlModeling.btnResetView") }}
              </el-button>
              <el-button
                size="small"
                :disabled="!currentModelPath"
                @click="handleExportModel"
              >
                <el-icon><Download /></el-icon
                >{{ t("nlModeling.btnExportModel") }}
              </el-button>
            </div>
          </div>
          <ParamSliderPanel
            :script="currentScript"
            :parameters="currentScriptParams"
            @regenerated="handleParamsRegenerated"
          />
          <div class="preview-viewport">
            <SimulationViewer
              v-if="currentModelPath"
              :model-path="currentModelPath"
              :show-grid="true"
              :show-axes="true"
              height="100%"
            />
            <div v-else class="preview-placeholder">
              <el-icon :size="64">
                <Box />
              </el-icon>
              <p>{{ t("nlModeling.placeholderHint") }}</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { Refresh, Download, Box, Switch } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { useI18n } from "vue-i18n";
import NLInputPanel from "@/components/nl2cad/NLInputPanel.vue";
import WorkflowGuide from "@/components/nl2cad/WorkflowGuide.vue";
import ParamSliderPanel from "@/components/nl2cad/ParamSliderPanel.vue";
import SimulationViewer from "@/components/simulation/SimulationViewer.vue";
import {
  generateModel as apiGenerateModel,
  generateProcessPlanning as apiGenerateProcessPlanning,
  generateNC as apiGenerateNC,
} from "@/api/nl2cad";
import type {
  CADDimensions,
  CADParams,
  ProcessConfig,
  ProcessPlan,
} from "@/types/nl2cad";

const { t } = useI18n();
const router = useRouter();

const viewMode = ref<"guide" | "chat">("guide");
const currentModelPath = ref<string>("");
const currentParams = ref<CADParams | null>(null);
// 参数化直调：generate 返回的等价脚本 + 参数表（空脚本时滑杆面板不渲染）
const currentScript = ref<string>("");
const currentScriptParams = ref<Record<string, number>>({});
// 最近一次生成的 NC 代码（跳转仿真页时交接给切削仿真预填）
const currentNcCode = ref<string>("");

function toggleViewMode() {
  viewMode.value = viewMode.value === "guide" ? "chat" : "guide";
}

// 工作流向导事件处理
function handleStepChange(_step: number) {
  // 步骤切换回调（由子组件触发，无需额外处理）
}

function handleParamsExtracted(params: CADParams) {
  currentParams.value = params;
}

async function handleGenerateModel(params: CADParams) {
  try {
    const data = await apiGenerateModel({
      description:
        (params.description as string) || t("nlModeling.defaultDescription"),
      output_format: "stl",
    });
    currentModelPath.value = data.model_path;
    currentParams.value = data.params as CADParams;
    currentScript.value = data.script || "";
    currentScriptParams.value = data.parameters || {};
    ElMessage.success(t("nlModeling.msgModelSuccess"));
  } catch (error) {
    console.error("Generate model failed:", error);
    ElMessage.error(t("nlModeling.msgModelFailed"));
  }
}

// 滑杆调参重执行完成：换模型路径 + 把脚本参数回写 currentParams，
// 否则后续"生成工艺规划"仍用调参前的 dimensions，下游工艺/NC 与模型脱节
function handleParamsRegenerated(
  modelPath: string,
  params: Record<string, number>,
) {
  currentModelPath.value = modelPath;
  currentScriptParams.value = params;
  syncDimensionsFromScriptParams(params);
}

// 脚本参数按形状语义回写 dimensions，推导规则与后端 _validate_params
// 的归一化一致：sphere/cylinder 的 radius 派生 length/width = 2r；
// cone 的 bottom/top_radius 分别派生 width/length
function syncDimensionsFromScriptParams(scriptParams: Record<string, number>) {
  const cadParams = currentParams.value;
  if (!cadParams) return;
  const dims: CADDimensions = { ...cadParams.dimensions };
  if ("radius" in scriptParams) {
    dims.radius = scriptParams.radius;
    dims.length = scriptParams.radius * 2;
    dims.width = scriptParams.radius * 2;
    if (cadParams.shape_type === "sphere")
      dims.height = scriptParams.radius * 2;
  }
  if ("bottom_radius" in scriptParams)
    dims.width = scriptParams.bottom_radius * 2;
  if ("top_radius" in scriptParams) {
    dims.length = scriptParams.top_radius * 2;
    dims.radius = scriptParams.top_radius;
  }
  if ("length" in scriptParams) dims.length = scriptParams.length;
  if ("width" in scriptParams) dims.width = scriptParams.width;
  if ("height" in scriptParams) dims.height = scriptParams.height;
  currentParams.value = { ...cadParams, dimensions: dims };
}

async function handleGenerateProcess(
  config: ProcessConfig & { process_plan?: ProcessPlan },
) {
  try {
    await apiGenerateProcessPlanning({
      cad_params: currentParams.value || {},
      material: config.material || "steel",
      machine_type: config.machine_type || "cnc_mill",
      precision: config.precision || "finish",
    });
    ElMessage.success(t("nlModeling.msgProcessSuccess"));
  } catch (error) {
    console.error("Generate process failed:", error);
    ElMessage.error(t("nlModeling.msgProcessFailed"));
  }
}

async function handleGenerateNC(payload: {
  nc_code: string;
  process_plan?: ProcessPlan;
}) {
  try {
    const data = await apiGenerateNC({
      process_plan: payload.process_plan || {},
      machine_type: "cnc_mill",
    });
    currentNcCode.value = data.nc_code || "";
    ElMessage.success(t("nlModeling.msgNcSuccess"));
  } catch (error) {
    console.error("Generate NC failed:", error);
    ElMessage.error(t("nlModeling.msgNcFailed"));
  }
}

function handleStartSimulation() {
  // 跳转到仿真模拟页对当前模型/G代码执行切削仿真
  if (!currentModelPath.value && !currentNcCode.value) {
    ElMessage.warning(t("nlModeling.msgModelFirst"));
    return;
  }
  // 模型/G代码经 sessionStorage 交接（G代码可能超 URL 长度限制），
  // 仿真页 onMounted 消费后即清除
  try {
    sessionStorage.setItem(
      "nl2cad_simulation_handoff",
      JSON.stringify({
        model_path: currentModelPath.value,
        gcode: currentNcCode.value,
      }),
    );
  } catch {
    // sessionStorage 不可用时静默降级：仅跳转，不预填
  }
  router.push("/simulation");
}

function handleComplete() {
  ElMessage.success(t("nlModeling.msgComplete"));
}

// 对话模式事件处理
function handleModelGenerated(
  modelPath: string,
  params: CADParams,
  script?: string,
  scriptParams?: Record<string, number>,
) {
  currentModelPath.value = modelPath;
  currentParams.value = params;
  currentScript.value = script || "";
  currentScriptParams.value = scriptParams || {};
}

function handleView3D(modelPath: string) {
  currentModelPath.value = modelPath;
}

function handleResetView() {
  // 重置当前模型视图状态（模型路径与参数）
  currentModelPath.value = "";
  currentParams.value = null;
  currentScript.value = "";
  currentScriptParams.value = {};
  ElMessage.success(t("nlModeling.msgViewReset"));
}

function handleExportModel() {
  if (!currentModelPath.value) return;

  const link = document.createElement("a");
  link.href = currentModelPath.value;
  link.download = currentModelPath.value.split("/").pop() || "model.stl";
  link.click();
}
</script>

<style scoped>
.nl-modeling-page {
  padding: var(--page-padding);
  height: 100%;
  display: flex;
  flex-direction: column;
}

.page-header {
  margin-bottom: 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.page-header h1 {
  font-size: 22px;
  font-weight: 700;
  color: var(--text-primary);
  margin: 0;
}

.subtitle {
  font-size: 13px;
  color: var(--text-tertiary);
  margin: 4px 0 0;
}

.page-header__actions {
  display: flex;
  gap: 8px;
}

/* Guide mode */
.guide-mode {
  flex: 1;
  min-height: 0;
  background: var(--bg-card);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-lg);
  overflow: hidden;
}

/* Chat mode */
.chat-mode {
  flex: 1;
  min-height: 0;
}

.modeling-content {
  flex: 1;
  display: flex;
  gap: 24px;
  min-height: 0;
  height: 100%;
}

.input-panel {
  width: 400px;
  flex-shrink: 0;
  background: var(--bg-card);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-lg);
  overflow: hidden;
}

.preview-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: var(--bg-card);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-lg);
  overflow: hidden;
}

.preview-header {
  padding: 16px 20px;
  border-bottom: 1px solid var(--border-light);
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.preview-header h3 {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.preview-actions {
  display: flex;
  gap: 8px;
}

.preview-viewport {
  flex: 1;
  position: relative;
  background: var(--bg-secondary);
}

.preview-placeholder {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  text-align: center;
  color: var(--text-tertiary);
}

.preview-placeholder .el-icon {
  margin-bottom: 16px;
  opacity: 0.3;
}

.preview-placeholder p {
  font-size: 14px;
  margin: 0;
}
</style>
