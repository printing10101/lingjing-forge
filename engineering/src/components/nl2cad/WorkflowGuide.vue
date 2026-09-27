<template>
  <div class="workflow-guide">
    <WorkflowGuideStepsIndicator
      :steps="steps"
      :current-step="currentStep"
      @step-click="handleStepClick"
    />

    <!-- 步骤内容区域 -->
    <div class="step-content">
      <!-- 步骤1：自然语言描述 -->
      <Step1DescriptionPanel
        v-if="currentStep === 0"
        :model-value="nlDescription"
        :examples="examples"
        @update:model-value="nlDescription = $event"
        @fill-example="fillExample"
        @next="handleNextStep"
      />

      <!-- 步骤2：参数确认 -->
      <Step2ParamsPanel
        v-if="currentStep === 1"
        :params="extractedParams"
        @prev="currentStep--"
        @generate="handleGenerateModel"
        @update:shape-type="extractedParams.shape_type = $event"
        @update:dimension="handleUpdateDimension"
        @update:material="extractedParams.material = $event"
      />

      <!-- 步骤3：模型预览 -->
      <Step3PreviewPanel
        v-if="currentStep === 2"
        :model-generated="modelGenerated"
        :params="extractedParams"
        @prev="currentStep--"
        @next="handleNextStep"
      >
        <template #3d-viewer>
          <slot name="3d-viewer" />
        </template>
      </Step3PreviewPanel>

      <!-- 步骤4：工艺规划 -->
      <Step4ProcessPanel
        v-if="currentStep === 3"
        :config="processConfig"
        @prev="currentStep--"
        @generate="handleGenerateProcess"
        @update:material="processConfig.material = $event"
        @update:machine-type="processConfig.machine_type = $event"
        @update:precision="processConfig.precision = $event"
      />

      <!-- 步骤5：NC代码 -->
      <Step5NcCodePanel
        v-if="currentStep === 4"
        :nc-code-generated="ncCodeGenerated"
        :nc-code="ncCode"
        @prev="currentStep--"
        @next="handleNextStep"
        @copy-code="handleCopyCode"
        @download-code="handleDownloadCode"
      />

      <!-- 步骤6：仿真验证 -->
      <Step6SimulationPanel
        v-if="currentStep === 5"
        @prev="currentStep--"
        @complete="handleComplete"
        @start-simulation="handleStartSimulation"
        @pause-simulation="handlePauseSimulation"
        @reset-simulation="handleResetSimulation"
        @download-animation="handleDownloadAnimation"
      >
        <template #simulation-viewer>
          <slot name="simulation-viewer" />
        </template>
      </Step6SimulationPanel>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed } from "vue";
import { useI18n } from "vue-i18n";
import { ElMessage } from "element-plus";
import WorkflowGuideStepsIndicator from "./WorkflowGuideStepsIndicator.vue";
import Step1DescriptionPanel from "@/components/nl2cad/steps/Step1DescriptionPanel.vue";
import Step2ParamsPanel from "@/components/nl2cad/steps/Step2ParamsPanel.vue";
import Step3PreviewPanel from "@/components/nl2cad/steps/Step3PreviewPanel.vue";
import Step4ProcessPanel from "@/components/nl2cad/steps/Step4ProcessPanel.vue";
import Step5NcCodePanel from "@/components/nl2cad/steps/Step5NcCodePanel.vue";
import Step6SimulationPanel from "@/components/nl2cad/steps/Step6SimulationPanel.vue";
import {
  extractParams as apiExtractParams,
  generateModel as apiGenerateModel,
  generateProcessPlanning as apiGenerateProcessPlanning,
  generateNC as apiGenerateNC,
  exportSimulationAnimation as apiExportAnimation,
} from "@/api/nl2cad";
import type { CADParams, ProcessConfig, ProcessPlan } from "@/types/nl2cad";

// Props & Emits
const props = defineProps<{
  initialDescription?: string;
}>();

const emit = defineEmits<{
  (e: "step-change", step: number): void;
  (e: "params-extracted", params: CADParams): void;
  (e: "generate-model", params: CADParams): void;
  (
    e: "generate-process",
    payload: ProcessConfig & { process_plan?: ProcessPlan },
  ): void;
  (
    e: "generate-nc",
    payload: { nc_code: string; process_plan?: ProcessPlan },
  ): void;
  (e: "start-simulation"): void;
  (e: "complete"): void;
}>();

const { t } = useI18n();

// Steps definition
const steps = computed(() => [
  {
    id: "select-model",
    title: t("workflowGuide.step1Title"),
    description: t("workflowGuide.step1Desc"),
    clickable: true,
  },
  {
    id: "config-params",
    title: t("workflowGuide.step2Title"),
    description: t("workflowGuide.step2Desc"),
    clickable: true,
  },
  {
    id: "generate-cad",
    title: t("workflowGuide.step3Title"),
    description: t("workflowGuide.step3Desc"),
    clickable: false,
  },
  {
    id: "output-nc",
    title: t("workflowGuide.step4Title"),
    description: t("workflowGuide.step4Desc"),
    clickable: true,
  },
  {
    id: "validate",
    title: t("workflowGuide.step5Title"),
    description: t("workflowGuide.step5Desc"),
    clickable: false,
  },
  {
    id: "review",
    title: t("workflowGuide.step6Title"),
    description: t("workflowGuide.step6Desc"),
    clickable: false,
  },
]);

// Examples
const examples = computed(() => [
  { text: t("workflowGuide.example1") },
  { text: t("workflowGuide.example2") },
  { text: t("workflowGuide.example3") },
  { text: t("workflowGuide.example4") },
]);

// Reactive state
const currentStep = ref(0);
const nlDescription = ref(props.initialDescription || "");
const extractedParams = reactive<CADParams>({
  shape_type: "box",
  dimensions: { length: 50, width: 30, height: 20 },
  material: "steel",
  confidence: 0.85,
});
const modelGenerated = ref(false);
const processConfig = reactive<ProcessConfig>({
  material: "aluminum_6061",
  machine_type: "cnc_mill",
  precision: "finish",
});
const ncCodeGenerated = ref(false);
const ncCode = ref("");

// Methods
function handleStepClick(index: number) {
  if (steps.value[index].clickable && index <= currentStep.value) {
    currentStep.value = index;
    emit("step-change", index);
  }
}

function handleUpdateDimension(key: string, value: number) {
  const dims = extractedParams.dimensions as Record<string, number | undefined>;
  dims[key] = value;
}

function fillExample(text: string) {
  nlDescription.value = text;
}

function handleNextStep() {
  if (currentStep.value === 0) {
    // 从NL描述提取参数
    extractParamsFromNL();
  } else if (currentStep.value === 2) {
    currentStep.value = 3;
    emit("step-change", 3);
  } else if (currentStep.value === 4) {
    currentStep.value = 5;
    emit("step-change", 5);
  } else {
    currentStep.value++;
    emit("step-change", currentStep.value);
  }
}

async function extractParamsFromNL() {
  try {
    const data = await apiExtractParams({ description: nlDescription.value });
    Object.assign(extractedParams, data.params);

    currentStep.value = 1;
    emit("step-change", 1);
    emit("params-extracted", extractedParams);
    ElMessage.success(t("workflowGuide.msgParamsExtracted"));
  } catch (error) {
    console.error("Extract params failed:", error);
    ElMessage.error(t("workflowGuide.msgParamsExtractFailed"));
  }
}

async function handleGenerateModel() {
  try {
    const data = await apiGenerateModel({
      description: nlDescription.value,
      output_format: "stl",
    });
    emit("generate-model", { ...extractedParams, model_path: data.model_path });

    modelGenerated.value = true;
    currentStep.value = 2;
    emit("step-change", 2);
    ElMessage.success(t("workflowGuide.msgModelGenerated"));
  } catch (error) {
    console.error("Generate model failed:", error);
    ElMessage.error(t("workflowGuide.msgModelGenerateFailed"));
  }
}

async function handleGenerateProcess() {
  try {
    const data = await apiGenerateProcessPlanning({
      cad_params: extractedParams,
      material: processConfig.material,
      machine_type: processConfig.machine_type,
      precision: processConfig.precision,
    });
    emit("generate-process", {
      ...processConfig,
      process_plan: data.process_plan,
    });

    currentStep.value = 4;
    emit("step-change", 4);

    // 生成NC代码
    await generateNCCode(data.process_plan);
  } catch (error) {
    console.error("Generate process failed:", error);
    ElMessage.error(t("workflowGuide.msgProcessFailed"));
  }
}

async function generateNCCode(processPlan?: ProcessPlan) {
  try {
    const data = await apiGenerateNC({
      process_plan: processPlan || {},
      machine_type: processConfig.machine_type,
    });
    ncCode.value = data.nc_code;
    ncCodeGenerated.value = true;
    emit("generate-nc", { nc_code: data.nc_code, process_plan: processPlan });
    ElMessage.success(t("workflowGuide.msgNcGenerated"));
  } catch (error) {
    console.error("Generate NC code failed:", error);
    ElMessage.error(t("workflowGuide.msgNcGenerateFailed"));
  }
}

function handleCopyCode() {
  navigator.clipboard.writeText(ncCode.value);
  ElMessage.success(t("workflowGuide.msgCodeCopied"));
}

function handleDownloadCode() {
  const blob = new Blob([ncCode.value], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "part_program.gcode";
  a.click();
  URL.revokeObjectURL(url);
  ElMessage.success(t("workflowGuide.msgCodeDownloaded"));
}

function handleStartSimulation() {
  emit("start-simulation");
  ElMessage.info(t("workflowGuide.msgSimStarted"));
}

function handlePauseSimulation() {
  ElMessage.info(t("workflowGuide.msgSimPaused"));
}

function handleResetSimulation() {
  ElMessage.info(t("workflowGuide.msgSimReset"));
}

async function handleDownloadAnimation() {
  try {
    // 通过统一 http 客户端调用后端仿真动画导出接口（返回 Blob）
    const blob = await apiExportAnimation({
      nc_code: ncCode.value,
      format: "gif",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "simulation_animation.gif";
    a.click();
    URL.revokeObjectURL(url);

    ElMessage.success(t("workflowGuide.msgAnimationDownloaded"));
  } catch (error) {
    console.error("Download animation failed:", error);
    ElMessage.error(t("workflowGuide.msgAnimationDownloadFailed"));
  }
}

function handleComplete() {
  emit("complete");
  ElMessage.success(t("workflowGuide.msgWorkflowCompleted"));
}
</script>

<style scoped>
.workflow-guide {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--bg-primary);
}

.step-content {
  flex: 1;
  overflow-y: auto;
  padding: 24px 32px;
}
</style>
