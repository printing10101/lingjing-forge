<template>
  <div class="app-file-dialogs">
    <!-- ==================== 新建工程对话框 ==================== -->
    <el-dialog
      v-model="showNewDialog"
      :title="$t('app.newDialogTitle')"
      width="480px"
      :close-on-click-modal="false"
    >
      <el-form :model="newForm" label-width="80px">
        <el-form-item :label="$t('app.projectName')" required>
          <el-input
            v-model="newForm.name"
            maxlength="128"
            :placeholder="$t('app.projectNamePlaceholder')"
          />
        </el-form-item>
        <el-form-item :label="$t('common.author')">
          <el-input
            v-model="newForm.author"
            maxlength="64"
            :placeholder="$t('app.authorPlaceholder')"
          />
        </el-form-item>
        <el-form-item :label="$t('common.description')">
          <el-input
            v-model="newForm.description"
            type="textarea"
            :rows="3"
            maxlength="512"
            :placeholder="$t('app.descriptionPlaceholder')"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showNewDialog = false">
          {{ $t("common.cancel") }}
        </el-button>
        <el-button type="primary" :loading="creating" @click="handleCreate">
          {{ $t("app.createProject") }}
        </el-button>
      </template>
    </el-dialog>

    <!-- ==================== 打开工程对话框 ==================== -->
    <el-dialog
      v-model="showOpenDialog"
      :title="$t('app.openDialogTitle')"
      width="600px"
      :close-on-click-modal="false"
    >
      <el-tabs v-model="openTab">
        <el-tab-pane :label="$t('app.localTab')" name="local">
          <div v-if="openListLoading" style="text-align: center; padding: 20px">
            <el-icon class="is-loading">
              <Loading />
            </el-icon>
            {{ $t("common.loading") }}
          </div>
          <el-table
            v-else
            :data="projectStore.projectList"
            height="300"
            highlight-current-row
            stripe
            size="small"
            @row-dblclick="handleOpenFromList"
            @current-change="onOpenSelectChange"
          >
            <el-table-column
              prop="name"
              :label="$t('app.projectNameCol')"
              min-width="150"
            />
            <el-table-column :label="$t('app.modifiedTimeCol')" width="170">
              <template #default="{ row }">
                {{ formatDate(row.modified_at) }}
              </template>
            </el-table-column>
            <el-table-column :label="$t('app.sizeCol')" width="90">
              <template #default="{ row }">
                {{ formatSize(row.file_size) }}
              </template>
            </el-table-column>
            <el-table-column :label="$t('app.resourceCol')" width="60">
              <template #default="{ row }">
                {{ row.resource_count }}
              </template>
            </el-table-column>
            <el-table-column :label="$t('common.operation')" width="120">
              <template #default="{ row }">
                <el-button
                  size="small"
                  text
                  type="primary"
                  @click="handleOpenFromList(row as ProjectSummary)"
                >
                  {{ $t("app.open") }}
                </el-button>
                <el-button
                  size="small"
                  text
                  type="danger"
                  @click="handleDeleteProject(row as ProjectSummary)"
                >
                  {{ $t("common.delete") }}
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
        <el-tab-pane :label="$t('app.importTab')" name="import">
          <el-upload
            drag
            :auto-upload="false"
            :limit="1"
            accept=".vrm"
            :on-change="handleFileSelected"
            :file-list="importFileList"
          >
            <el-icon class="el-icon--upload">
              <UploadFilled />
            </el-icon>
            <div class="el-upload__text">
              {{ $t("app.importVrmHint") }}
              <em>{{ $t("app.importVrmClick") }}</em>
            </div>
          </el-upload>
        </el-tab-pane>
      </el-tabs>
      <template #footer>
        <el-button @click="showOpenDialog = false">
          {{ $t("common.cancel") }}
        </el-button>
        <el-button
          type="primary"
          :loading="opening"
          :disabled="!canOpen"
          @click="handleOpen"
        >
          {{ $t("app.openProject") }}
        </el-button>
      </template>
    </el-dialog>

    <!-- ==================== 另存为对话框 ==================== -->
    <el-dialog
      v-model="showSaveAsDialog"
      :title="$t('app.saveAsDialogTitle')"
      width="420px"
      :close-on-click-modal="false"
    >
      <el-form :model="saveAsForm" label-width="80px">
        <el-form-item :label="$t('app.fileName')" required>
          <el-input
            v-model="saveAsForm.outputName"
            maxlength="128"
            :placeholder="$t('app.fileNamePlaceholder')"
          >
            <template #append> .vrm </template>
          </el-input>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showSaveAsDialog = false">
          {{ $t("common.cancel") }}
        </el-button>
        <el-button type="primary" :loading="saving" @click="handleSaveAs">
          {{ $t("app.saveAsBtn") }}
        </el-button>
      </template>
    </el-dialog>

    <!-- ==================== 未保存提示对话框 ==================== -->
    <el-dialog
      v-model="showUnsavedDialog"
      :title="$t('app.unsavedTitle')"
      width="400px"
    >
      <p>{{ $t("app.unsavedMessage") }}</p>
      <template #footer>
        <el-button @click="discardAndProceed">
          {{ $t("common.discard") }}
        </el-button>
        <el-button type="primary" :loading="saving" @click="saveAndProceed">
          {{ $t("common.saveAndContinue") }}
        </el-button>
        <el-button @click="cancelProceed">
          {{ $t("common.cancel") }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * 应用文件对话框（App.vue 拆分子组件）
 *
 * 自包含 4 个工程文件对话框（新建/打开/另存为/未保存确认）+ save 命令。
 * 通过 openCommand(cmd) 由父组件（App.vue）的 file-command 事件驱动，
 * 内部状态与处理逻辑完全内聚。
 */
import { computed, reactive, ref } from "vue";
import { Loading, UploadFilled } from "@element-plus/icons-vue";
import { useProjectStore } from "@/stores/project";
import { useVersionStore } from "@/stores/version";
import { useStepImportStore } from "@/stores/stepImport";
import { useDxfImportStore } from "@/stores/dxfImport";
import type { ProjectSummary } from "@/types";
import type { UploadUserFile } from "element-plus";
import { formatFileSize } from "@/utils/formatters";

const projectStore = useProjectStore();
const versionStore = useVersionStore();
const stepImportStore = useStepImportStore();
const dxfImportStore = useDxfImportStore();

// 对话框可见性
const showNewDialog = ref(false);
const showOpenDialog = ref(false);
const showSaveAsDialog = ref(false);
const showUnsavedDialog = ref(false);

// 表单状态
const openTab = ref("local");
const openListLoading = ref(false);
const selectedSummary = ref<ProjectSummary | null>(null);
const newForm = reactive({ name: "", author: "", description: "" });
const saveAsForm = reactive({ outputName: "" });
const importFileList = ref<UploadUserFile[]>([]);

// 提交状态
const creating = ref(false);
const opening = ref(false);
const saving = ref(false);

// 待执行的未保存命令（在用户选择保存/放弃后执行）
const pendingFileCommand = ref("");

const canOpen = computed(() => {
  if (openTab.value === "local") return selectedSummary.value !== null;
  return importFileList.value.length > 0;
});

// 工具函数
function formatDate(iso: string) {
  if (!iso) return "-";
  const d = new Date(iso);
  const locale = localStorage.getItem("app_locale") || "zh-CN";
  return d.toLocaleString(locale === "en" ? "en-US" : "zh-CN", {
    hour12: false,
  });
}

const formatSize = (bytes: number): string => formatFileSize(bytes);

// 命令入口（由 App.vue file-command 驱动）
function handleFileCommand(cmd: string) {
  if (projectStore.isModified && (cmd === "new" || cmd === "open")) {
    pendingFileCommand.value = cmd;
    showUnsavedDialog.value = true;
    return;
  }
  executeCommand(cmd);
}

function executeCommand(cmd: string) {
  switch (cmd) {
    case "new":
      newForm.name = "";
      newForm.author = "";
      newForm.description = "";
      showNewDialog.value = true;
      break;
    case "open":
      openTab.value = "local";
      selectedSummary.value = null;
      importFileList.value = [];
      openListLoading.value = true;
      projectStore.fetchProjectList().finally(() => {
        openListLoading.value = false;
      });
      showOpenDialog.value = true;
      break;
    case "save":
      saving.value = true;
      projectStore.saveProject().finally(() => {
        saving.value = false;
      });
      break;
    case "save-as":
      saveAsForm.outputName = projectStore.projectName;
      showSaveAsDialog.value = true;
      break;
    case "download":
      if (projectStore.currentFilePath) {
        projectStore.downloadProject(
          projectStore.currentFilePath.split("/").pop() ||
            projectStore.currentFilePath.split("\\").pop() ||
            "project.vrm",
        );
      }
      break;
    case "import-step":
      stepImportStore.showDialog = true;
      break;
    case "import-dxf":
      dxfImportStore.openDialog();
      break;
  }
}

// 新建
async function handleCreate() {
  if (!newForm.name.trim()) return;
  creating.value = true;
  const ok = await projectStore.createProject({
    name: newForm.name.trim(),
    author: newForm.author.trim(),
    description: newForm.description.trim(),
  });
  creating.value = false;
  if (ok) showNewDialog.value = false;
}

// 打开
async function handleOpenFromList(row: ProjectSummary) {
  const ok = await projectStore.openProject(row.path);
  if (ok) showOpenDialog.value = false;
}

function onOpenSelectChange(row: ProjectSummary | null) {
  selectedSummary.value = row;
}

function handleFileSelected(file: { raw?: File; name?: string }) {
  if (!file.raw) {
    importFileList.value = [];
    return;
  }
  importFileList.value = [
    {
      name: file.name ?? file.raw.name,
      raw: file.raw,
    },
  ] as UploadUserFile[];
}

async function handleOpen() {
  if (openTab.value === "local" && selectedSummary.value) {
    opening.value = true;
    const ok = await projectStore.openProject(selectedSummary.value.path);
    opening.value = false;
    if (ok) showOpenDialog.value = false;
  } else if (openTab.value === "import" && importFileList.value[0]?.raw) {
    opening.value = true;
    const file = importFileList.value[0].raw;
    const reader = new FileReader();
    reader.onload = async () => {
      const base64 = (reader.result as string).split(",")[1];
      const ok = await projectStore.openProject(undefined, base64);
      opening.value = false;
      if (ok) showOpenDialog.value = false;
    };
    reader.readAsDataURL(file);
  }
}

async function handleDeleteProject(row: ProjectSummary) {
  await projectStore.deleteProject(row.name + ".vrm");
  await projectStore.fetchProjectList();
}

// 另存为
async function handleSaveAs() {
  if (!saveAsForm.outputName.trim()) return;
  saving.value = true;
  const ok = await projectStore.saveAsProject(
    saveAsForm.outputName.trim() + ".vrm",
  );
  saving.value = false;
  if (ok) showSaveAsDialog.value = false;
}

// 未保存确认
async function saveAndProceed() {
  saving.value = true;
  await projectStore.saveProject();
  saving.value = false;
  showUnsavedDialog.value = false;
  executeCommand(pendingFileCommand.value);
}

function discardAndProceed() {
  projectStore.isModified = false;
  showUnsavedDialog.value = false;
  executeCommand(pendingFileCommand.value);
}

function cancelProceed() {
  showUnsavedDialog.value = false;
  pendingFileCommand.value = "";
}

// 刷新（版本检查）
function handleRefresh() {
  versionStore.fetchVersionInfo();
  versionStore.checkConsistency();
}

defineExpose({
  handleFileCommand,
  handleRefresh,
});
</script>
