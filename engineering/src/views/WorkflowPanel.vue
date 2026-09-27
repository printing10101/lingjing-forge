<template>
  <div class="workflow-panel-page">
    <!-- ===== Page Header ===== -->
    <WorkflowPageHeader
      :loading="loading"
      :can-cancel="canCancel"
      :can-resume="canResume"
      :current-run-id="currentRunId"
      @refresh="handleRefresh"
      @open-submit="openSubmitDialog"
      @cancel-current="handleCancelCurrent"
      @open-resume="openResumeDialog"
      @delete-current="handleDeleteCurrent"
    />

    <!-- ===== Main Layout: List | DAG + Events ===== -->
    <div class="workflow-main">
      <!-- ===== Left: Workflow List ===== -->
      <WorkflowListPanel
        :workflows="workflows"
        :loading="loading"
        :status-filter="statusFilter"
        :status-options="statusOptions"
        :current-page="currentPage"
        :page-size="pageSize"
        :total-count="totalCount"
        :current-run-id="currentRunId"
        @update:status-filter="handleFilterChange"
        @select="handleSelectWorkflow"
        @update:current-page="handlePageChange"
      />

      <!-- ===== Right: DAG Visualization + Event Log ===== -->
      <div class="workflow-detail-panel">
        <WorkflowDag
          :nodes="dagLayout.nodes"
          :edges="dagLayout.edges"
          :width="dagLayout.width"
          :height="dagLayout.height"
          :selected-node-id="selectedNodeId"
          :node-width="nodeWidth"
          :node-height="nodeHeight"
          :spec="currentSpec"
          :title="t('workflowPanel.dagTitle')"
          :empty-text="t('workflowPanel.emptyNoSelection')"
          :current-display-status="currentDisplayStatus"
          :stream-status-text="streamStatusText"
          :is-stream-connected="stream.isConnected.value"
          :is-stream-done="stream.isDone.value"
          :current-run-id="currentRunId"
          :node-statuses="nodeStatusMap"
          @update:selected-node-id="selectedNodeId = $event"
          @node-click="handleNodeClick"
        />

        <WorkflowEventLog
          :events="stream.events.value"
          :title="t('workflowPanel.eventLogTitle')"
          :empty-text="t('workflowPanel.emptyNoEvents')"
          :btn-clear-text="t('workflowPanel.btnClearEvents')"
          @clear="stream.reset"
        />
      </div>
    </div>

    <!-- ===== Submit / Resume Dialog ===== -->
    <WorkflowSubmitDialog
      :visible="submitDialogVisible"
      :mode="submitMode"
      :title="submitDialogTitle"
      :confirm-button-text="submitConfirmButtonText"
      :form="submitForm"
      :builtin-templates="builtinTemplates"
      :validating="validating"
      :submitting="submitting"
      :form-template-label="t('workflowPanel.formTemplateName')"
      :form-template-placeholder="t('workflowPanel.formTemplatePlaceholder')"
      :form-spec-label="t('workflowPanel.formSpec')"
      :form-spec-placeholder="t('workflowPanel.formSpecPlaceholder')"
      :form-owner-label="t('workflowPanel.formOwnerId')"
      :form-owner-placeholder="t('workflowPanel.formOwnerPlaceholder')"
      :btn-cancel-text="t('workflowPanel.btnCancelDialog')"
      :btn-validate-text="t('workflowPanel.btnValidate')"
      @update:visible="submitDialogVisible = $event"
      @submit="handleSubmit"
      @cancel="submitDialogVisible = false"
      @validate="handleValidate"
      @template-select="handleTemplateSelect"
      @update:form="submitForm = $event"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useWorkflow } from '@/composables/useWorkflow'
// 子组件
import WorkflowPageHeader from '@/components/workflow/WorkflowPageHeader.vue'
import WorkflowListPanel from '@/components/workflow/WorkflowListPanel.vue'
import WorkflowDag from '@/components/workflow/WorkflowDag.vue'
import WorkflowEventLog from '@/components/workflow/WorkflowEventLog.vue'
import WorkflowSubmitDialog from '@/components/workflow/WorkflowSubmitDialog.vue'
import type { WorkflowSpec, TaskStatus } from '@/contracts/task'
import { useDagLayout } from '@/composables/useDagLayout'

const { t } = useI18n()

// useWorkflow composable 接入
const {
  workflows,
  loading,
  totalCount,
  currentPage,
  pageSize,
  statusFilter,
  loadWorkflows,
  removeWorkflow,
  currentRunId,
  currentStatus,
  submitWorkflow,
  resumeCurrentWorkflow,
  cancelCurrent,
  refreshCurrentStatus,
  selectWorkflow,
  stream,
  validate,
} = useWorkflow()

// 状态枚举
const statusOptions = [
  { value: 'pending', label: t('workflowPanel.statusPending') },
  { value: 'queued', label: t('workflowPanel.statusQueued') },
  { value: 'running', label: t('workflowPanel.statusRunning') },
  { value: 'completed', label: t('workflowPanel.statusCompleted') },
  { value: 'failed', label: t('workflowPanel.statusFailed') },
  { value: 'cancelled', label: t('workflowPanel.statusCancelled') },
  { value: 'skipped', label: t('workflowPanel.statusSkipped') },
]

// 内置模板（与后端 python/app/workflow/templates/builtin/*.yaml 对应）
// 提供下拉选择，用户选择后填充 spec 编辑器
const builtinTemplates = ref<Array<{ name: string; version: string; spec: WorkflowSpec }>>([])

const SAMPLE_TOOL_WEAR_SPEC: WorkflowSpec = JSON.parse('{"name": "刀具磨损预测流水线", "version": "1.0.0", "nodes": [{"node_id": "load_dataset", "task_type": "dataset_loader", "params": {"loader_type": "phm2010"}, "inputs": {}, "retry": 0, "timeout_seconds": 600}, {"node_id": "train_model", "task_type": "ltc_trainer", "params": {"model_type": "ltc", "epochs": 50}, "inputs": {"train_split": "${load_dataset.train_split}"}, "retry": 1, "timeout_seconds": 7200}, {"node_id": "evaluate_model", "task_type": "model_evaluator", "params": {"metrics": ["mae", "r2"]}, "inputs": {"test_split": "${load_dataset.test_split}", "trained_model": "${train_model.model_artifact}"}, "retry": 0, "timeout_seconds": 1800}, {"node_id": "generate_report", "task_type": "report_generator", "params": {"template": "tool_wear_evaluation.md"}, "inputs": {"metrics": "${evaluate_model.metrics_artifact}"}, "retry": 0, "timeout_seconds": 600}], "edges": [{"upstream": "load_dataset", "downstream": "train_model"}, {"upstream": "train_model", "downstream": "evaluate_model"}, {"upstream": "evaluate_model", "downstream": "generate_report"}], "inputs": {}, "outputs": {"wear_report": "${generate_report.report_artifact}"}, "metadata": {"max_concurrent": 2, "tags": ["tool_wear", "ltc"]}}')

function initBuiltinTemplates() {
  builtinTemplates.value = [
    { name: SAMPLE_TOOL_WEAR_SPEC.name, version: SAMPLE_TOOL_WEAR_SPEC.version, spec: SAMPLE_TOOL_WEAR_SPEC },
  ]
}

// 当前选中的工作流 spec（用于 DAG 可视化）
const currentSpec = computed<WorkflowSpec | null>(() => {
  if (!currentRunId.value) return null
  // 优先从列表中取 spec（完整结构），否则从 currentStatus 取
  const wf = workflows.value.find(w => w.id === currentRunId.value)
  if (wf?.spec) return wf.spec
  return null
})

const currentDisplayStatus = computed<string>(() => {
  // 优先采用 SSE 实时状态，其次持久化状态
  if (stream.currentStatus.value) return stream.currentStatus.value
  if (currentStatus.value?.status) return currentStatus.value.status
  const wf = workflows.value.find(w => w.id === currentRunId.value)
  return wf?.status ?? 'pending'
})

const canCancel = computed(() => {
  const s = currentDisplayStatus.value
  return Boolean(currentRunId.value && (s === 'running' || s === 'queued' || s === 'pending'))
})

const canResume = computed(() => {
  const s = currentDisplayStatus.value
  return Boolean(currentRunId.value && (s === 'failed' || s === 'cancelled'))
})

const streamStatusText = computed(() => {
  if (stream.isDone.value) return t('workflowPanel.streamDone')
  if (stream.isConnected.value) return t('workflowPanel.streamConnected')
  if (stream.error.value) return t('workflowPanel.streamError')
  return t('workflowPanel.streamIdle')
})

// 节点状态：合并 SSE nodeStatuses + 持久化 node_statuses
const nodeStatusMap = computed<Record<string, TaskStatus>>(() => {
  const map: Record<string, TaskStatus> = {}
  // SSE 节点状态（高优先级）
  if (stream.nodeStatuses.value) {
    for (const [k, v] of Object.entries(stream.nodeStatuses.value)) {
      map[k] = v as TaskStatus
    }
  }
  // 持久化节点状态（兜底）
  if (currentStatus.value?.node_statuses) {
    for (const [k, v] of Object.entries(currentStatus.value.node_statuses)) {
      if (!(k in map)) map[k] = v as TaskStatus
    }
  }
  return map
})

// DAG 分层布局（自实现，避免引入 dagre 依赖）
// 算法：Kahn 拓扑排序 + 按入度分层
const nodeWidth = 160
const nodeHeight = 76

const dagLayout = useDagLayout(() => currentSpec.value)

// 子组件已内置 isEdgeActive / getNodeStatus 逻辑

// 列表交互
function handleRefresh() {
  loadWorkflows()
  if (currentRunId.value) refreshCurrentStatus()
}

function handleFilterChange(value: string) {
  statusFilter.value = value
  currentPage.value = 1
  loadWorkflows()
}

function handlePageChange(page: number) {
  currentPage.value = page
  loadWorkflows()
}

async function handleSelectWorkflow(id: string) {
  try {
    await selectWorkflow(id)
  } catch (e) {
    console.warn('[WorkflowPanel] selectWorkflow failed:', e)
  }
}

async function handleCancelCurrent() {
  if (!currentRunId.value) return
  try {
    await ElMessageBox.confirm(
      t('workflowPanel.confirmCancel'),
      t('workflowPanel.warning'),
      { type: 'warning' },
    )
  } catch {
    return // 用户取消
  }
  try {
    await cancelCurrent()
    ElMessage.success(t('workflowPanel.msgCancelSuccess'))
  } catch (e) {
    console.warn('[WorkflowPanel] cancel failed:', e)
  }
}

async function handleDeleteCurrent() {
  if (!currentRunId.value) return
  try {
    await ElMessageBox.confirm(
      t('workflowPanel.confirmDelete'),
      t('workflowPanel.warning'),
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await removeWorkflow(currentRunId.value)
    ElMessage.success(t('workflowPanel.msgDeleteSuccess'))
  } catch (e) {
    console.warn('[WorkflowPanel] delete failed:', e)
  }
}

// 提交 / 续跑对话框
const submitDialogVisible = ref(false)
const submitMode = ref<'submit' | 'resume'>('submit')
const submitForm = ref({
  templateName: '',
  specYaml: '',
  ownerId: '',
})
const validating = ref(false)
const submitting = ref(false)
const selectedNodeId = ref<string>('')

function handleNodeClick(nodeId: string) {
  selectedNodeId.value = nodeId
}

const submitDialogTitle = computed(() =>
  submitMode.value === 'submit'
    ? t('workflowPanel.dialogSubmitTitle')
    : t('workflowPanel.dialogResumeTitle'),
)

const submitConfirmButtonText = computed(() =>
  submitMode.value === 'submit'
    ? t('workflowPanel.btnSubmitConfirm')
    : t('workflowPanel.btnResumeConfirm'),
)

function openSubmitDialog() {
  submitMode.value = 'submit'
  submitForm.value = { templateName: '', specYaml: '', ownerId: '' }
  submitDialogVisible.value = true
}

function openResumeDialog() {
  if (!currentRunId.value) return
  submitMode.value = 'resume'
  // 续跑时预填当前 spec
  const wf = workflows.value.find(w => w.id === currentRunId.value)
  if (wf?.spec) {
    submitForm.value = {
      templateName: '',
      specYaml: JSON.stringify(wf.spec, null, 2),
      ownerId: wf.owner_id ?? '',
    }
  } else {
    submitForm.value = { templateName: '', specYaml: '', ownerId: '' }
  }
  submitDialogVisible.value = true
}

function handleTemplateSelect(name: string) {
  if (!name) return
  const tpl = builtinTemplates.value.find(t => t.name === name)
  if (tpl) {
    submitForm.value.specYaml = JSON.stringify(tpl.spec, null, 2)
  }
}

function parseSpec(): WorkflowSpec | null {
  const text = submitForm.value.specYaml.trim()
  if (!text) {
    ElMessage.warning(t('workflowPanel.msgSpecEmpty'))
    return null
  }
  try {
    // 后端模板是 YAML，前端为简化依赖使用 JSON 解析；
    // 用户也可粘贴 YAML（需后端 /validate 端点解析，此处先尝试 JSON）
    const obj = JSON.parse(text) as WorkflowSpec
    if (!obj.name || !obj.nodes || !obj.edges) {
      ElMessage.error(t('workflowPanel.msgSpecInvalid'))
      return null
    }
    return obj
  } catch {
    // JSON 解析失败时尝试 YAML 简单解析（key: value 形式）
    ElMessage.error(t('workflowPanel.msgSpecParseError'))
    return null
  }
}

async function handleValidate() {
  const spec = parseSpec()
  if (!spec) return
  validating.value = true
  try {
    const result = await validate(spec)
    if (result.valid) {
      ElMessage.success(
        t('workflowPanel.msgValidateSuccess')
          .replace('{nodes}', String(result.node_count))
          .replace('{edges}', String(result.edge_count)),
      )
    } else {
      ElMessage.warning(t('workflowPanel.msgValidateFailed'))
    }
  } catch (e) {
    console.warn('[WorkflowPanel] validate failed:', e)
  } finally {
    validating.value = false
  }
}

async function handleSubmit() {
  const spec = parseSpec()
  if (!spec) return
  submitting.value = true
  try {
    if (submitMode.value === 'submit') {
      const runId = await submitWorkflow({
        spec,
        owner_id: submitForm.value.ownerId || undefined,
      })
      ElMessage.success(t('workflowPanel.msgSubmitSuccess').replace('{id}', runId.slice(0, 12)))
      submitDialogVisible.value = false
      await loadWorkflows()
    } else {
      // 续跑：基于当前 run_id
      if (!currentRunId.value) {
        ElMessage.warning(t('workflowPanel.msgNoCurrentRun'))
        return
      }
      const newRunId = await resumeCurrentWorkflow(currentRunId.value, {
        spec,
        owner_id: submitForm.value.ownerId || undefined,
      })
      ElMessage.success(t('workflowPanel.msgResumeSuccess').replace('{id}', newRunId.slice(0, 12)))
      submitDialogVisible.value = false
      await loadWorkflows()
    }
  } catch (e) {
    console.warn('[WorkflowPanel] submit/resume failed:', e)
  } finally {
    submitting.value = false
  }
}

// 生命周期
onMounted(() => {
  initBuiltinTemplates()
  loadWorkflows()
})

// 监听 SSE 终态：自动刷新列表与持久化状态
watch(
  () => stream.isDone.value,
  (done) => {
    if (done) {
      void loadWorkflows()
      if (currentRunId.value) void refreshCurrentStatus()
    }
  },
)
</script>

<style scoped>
.workflow-panel-page {
  padding: 16px;
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow: hidden;
}

/* ===== Main Layout ===== */
.workflow-main {
  flex: 1;
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: 12px;
  min-height: 0;
}

/* ===== Detail Panel ===== */
.workflow-detail-panel {
  display: grid;
  grid-template-rows: 1fr 200px;
  gap: 12px;
  min-height: 0;
}
</style>
