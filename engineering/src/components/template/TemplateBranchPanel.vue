<script setup lang="ts">
// 原 /branch-manager 页面内容，现挂载于模板中心"分支管理"Tab
import { ref, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { ElMessage, ElMessageBox } from "element-plus";
import { formatSecondsTimestamp } from "@/utils/formatters";
import { getBranchTypeTagType } from "@/utils/statusHelpers";
import http from "@/utils/http";
import { API_CONFIG, buildApiPath } from "@/config/api";

const { t } = useI18n();
const emit = defineEmits<{
  "open-detail": [branchId: string];
}>();

// 类型定义
interface Branch {
  id: string;
  name: string;
  type: string;
  base_branch: string | null;
  data: Record<string, unknown>;
  metadata: Record<string, unknown>;
  created_at: number;
  updated_at: number;
}

interface MergeForm {
  source_id: string;
  target_id: string;
  strategy: "overwrite" | "merge" | "rebase";
}

interface CreateForm {
  name: string;
  base_branch: string;
  type: "main" | "feature" | "hotfix";
  data: Record<string, unknown>;
}

const branches = ref<Branch[]>([]);
const loading = ref(false);
const createDialog = ref(false);
const mergeDialog = ref(false);
const mergeForm = ref<MergeForm>({
  source_id: "",
  target_id: "",
  strategy: "overwrite",
});
const createForm = ref<CreateForm>({
  name: "",
  base_branch: "",
  type: "main",
  data: {},
});
const typeFilter = ref("");
const branchDataInput = ref("{}");

async function fetchBranches() {
  loading.value = true;
  try {
    const url = typeFilter.value
      ? buildApiPath(
          API_CONFIG.V1,
          `/templates/branches/?type=${typeFilter.value}`,
        )
      : buildApiPath(API_CONFIG.V1, "/templates/branches/");
    const res = await http.get(url);
    if (res.data.code === "SUCCESS") branches.value = res.data.data;
  } catch (e: unknown) {
    // 辅助列表数据加载失败不弹窗，仅记录便于排查
    console.warn("[TemplateCenter] fetchBranches failed:", e);
  } finally {
    loading.value = false;
  }
}

async function createBranch() {
  let data: Record<string, unknown> = {};
  try {
    data = JSON.parse(branchDataInput.value || "{}");
  } catch (e: unknown) {
    // JSON 解析失败，使用空对象，记录便于用户排查输入格式
    console.warn("[TemplateCenter] createBranch JSON parse failed:", e);
    data = {};
  }
  try {
    await http.post(`${API_CONFIG.V1}/templates/branches/`, {
      name: createForm.value.name,
      base_branch: createForm.value.base_branch || null,
      data,
      metadata: { type: createForm.value.type },
    });
    createDialog.value = false;
    createForm.value = { name: "", base_branch: "", type: "main", data: {} };
    branchDataInput.value = "{}";
    fetchBranches();
  } catch (e: unknown) {
    // 用户主动创建分支失败需明确反馈
    console.error("[TemplateCenter] createBranch failed:", e);
    ElMessage.error(
      t("branchManager.msgCreateFailed") || "创建分支失败，请稍后重试",
    );
  }
}

async function mergeBranch() {
  try {
    await http.post(
      `${API_CONFIG.V1}/templates/branches/${mergeForm.value.source_id}/merge`,
      {
        target_id: mergeForm.value.target_id,
        strategy: mergeForm.value.strategy,
      },
    );
    mergeDialog.value = false;
    fetchBranches();
  } catch (e: unknown) {
    // 用户主动合并分支失败需明确反馈
    console.error("[TemplateCenter] mergeBranch failed:", e);
    ElMessage.error(
      t("branchManager.msgMergeFailed") || "合并分支失败，请稍后重试",
    );
  }
}

async function deleteBranch(branchId: string, type: string) {
  if (type === "main") {
    ElMessage.warning(t("branchManager.msgCannotDeleteMain"));
    return;
  }
  try {
    await ElMessageBox.confirm(
      t("branchManager.msgConfirmDelete"),
      t("branchManager.titleConfirmDelete"),
      {
        confirmButtonText: t("common.confirm"),
        cancelButtonText: t("common.cancel"),
        type: "warning",
      },
    );
    await http.delete(`${API_CONFIG.V1}/templates/branches/${branchId}`);
    fetchBranches();
  } catch (e: unknown) {
    // ElMessageBox.confirm 取消时返回 'cancel'，属于正常用户行为，需与真实错误区分
    const cancelled =
      e === "cancel" || (e instanceof Error && e.message.includes("cancel"));
    if (cancelled) return;
    console.error("[TemplateCenter] deleteBranch failed:", e);
    ElMessage.error(
      t("branchManager.msgDeleteFailed") || "删除分支失败，请稍后重试",
    );
  }
}

onMounted(fetchBranches);
</script>

<template>
  <div class="branch-panel">
    <div class="panel-toolbar">
      <el-select
        v-model="typeFilter"
        :placeholder="t('branchManager.placeholderAllTypes')"
        clearable
        style="width: 150px; margin-right: 12px"
        @change="fetchBranches"
      >
        <el-option :label="t('branchManager.labelAll')" value="" />
        <el-option :label="t('branchManager.labelMain')" value="main" />
        <el-option :label="t('branchManager.labelIndustry')" value="industry" />
        <el-option :label="t('branchManager.labelMaterial')" value="material" />
        <el-option :label="t('branchManager.labelProject')" value="project" />
        <el-option
          :label="t('branchManager.labelExperiment')"
          value="experiment"
        />
      </el-select>
      <el-button type="primary" @click="createDialog = true">
        {{ t("branchManager.btnCreateBranch") }}
      </el-button>
      <el-button @click="mergeDialog = true">
        {{ t("branchManager.btnMergeBranch") }}
      </el-button>
    </div>

    <div v-if="loading" class="loading">
      {{ t("common.loading") }}
    </div>

    <el-table v-else :data="branches" stripe>
      <el-table-column
        prop="name"
        :label="t('branchManager.labelBranchName')"
        min-width="150"
      />
      <el-table-column :label="t('branchManager.labelType')" width="120">
        <template #default="{ row }">
          <el-tag :type="getBranchTypeTagType(row.metadata?.type)" size="small">
            {{ row.metadata?.type || "unknown" }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column
        prop="base_branch"
        :label="t('branchManager.labelBaseBranch')"
        width="120"
      >
        <template #default="{ row }">
          {{ row.base_branch || "-" }}
        </template>
      </el-table-column>
      <el-table-column :label="t('branchManager.labelCommitLog')" width="100">
        <template #default="{ row }">
          {{ row.commit_log?.length || 0 }}
        </template>
      </el-table-column>
      <el-table-column :label="t('branchManager.labelUpdateTime')" width="180">
        <template #default="{ row }">
          {{ formatSecondsTimestamp(row.updated_at) }}
        </template>
      </el-table-column>
      <el-table-column
        :label="t('branchManager.labelOperation')"
        width="180"
        fixed="right"
      >
        <template #default="{ row }">
          <el-button size="small" @click="emit('open-detail', row.id)">
            {{ t("common.detail") }}
          </el-button>
          <el-button
            v-if="row.metadata?.type !== 'main'"
            size="small"
            type="danger"
            @click="deleteBranch(row.id, row.metadata?.type)"
          >
            {{ t("common.delete") }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-empty
      v-if="!loading && branches.length === 0"
      :description="t('branchManager.emptyNoBranch')"
    />

    <!-- Create Dialog -->
    <el-dialog
      v-model="createDialog"
      :title="t('branchManager.titleCreateBranch')"
      width="500px"
    >
      <el-form :model="createForm" label-width="80px">
        <el-form-item :label="t('branchManager.labelName')">
          <el-input
            v-model="createForm.name"
            :placeholder="t('branchManager.placeholderBranchName')"
          />
        </el-form-item>
        <el-form-item :label="t('branchManager.labelBaseBranch')">
          <el-select
            v-model="createForm.base_branch"
            clearable
            :placeholder="t('branchManager.placeholderNoMain')"
          >
            <el-option
              v-for="b in branches"
              :key="b.id"
              :label="b.name"
              :value="b.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('branchManager.labelType')">
          <el-select v-model="createForm.type">
            <el-option :label="t('branchManager.labelMain')" value="main" />
            <el-option
              :label="t('branchManager.labelIndustry')"
              value="industry"
            />
            <el-option
              :label="t('branchManager.labelMaterial')"
              value="material"
            />
            <el-option
              :label="t('branchManager.labelProject')"
              value="project"
            />
            <el-option
              :label="t('branchManager.labelExperiment')"
              value="experiment"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('branchManager.labelInitialData')">
          <el-input
            v-model="branchDataInput"
            type="textarea"
            :rows="6"
            :placeholder="t('branchManager.placeholderJsonData')"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialog = false">
          {{ t("common.cancel") }}
        </el-button>
        <el-button type="primary" @click="createBranch">
          {{ t("common.confirm") }}
        </el-button>
      </template>
    </el-dialog>

    <!-- Merge Dialog -->
    <el-dialog
      v-model="mergeDialog"
      :title="t('branchManager.titleMergeBranch')"
      width="500px"
    >
      <el-form :model="mergeForm" label-width="80px">
        <el-form-item :label="t('branchManager.labelSourceBranch')">
          <el-select v-model="mergeForm.source_id">
            <el-option
              v-for="b in branches.filter((x) => x.metadata?.type !== 'main')"
              :key="b.id"
              :label="b.name"
              :value="b.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('branchManager.labelTargetBranch')">
          <el-select v-model="mergeForm.target_id">
            <el-option
              v-for="b in branches"
              :key="b.id"
              :label="b.name"
              :value="b.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('branchManager.labelStrategy')">
          <el-select v-model="mergeForm.strategy">
            <el-option
              :label="t('branchManager.labelOverwrite')"
              value="overwrite"
            />
            <el-option
              :label="t('branchManager.labelDeepMerge')"
              value="deep_merge"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="mergeDialog = false">
          {{ t("common.cancel") }}
        </el-button>
        <el-button type="primary" @click="mergeBranch">
          {{ t("branchManager.btnMerge") }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.panel-toolbar {
  display: flex;
  align-items: center;
  margin-bottom: 12px;
}
.loading {
  text-align: center;
  padding: 40px;
  color: var(--text-tertiary);
}
</style>
