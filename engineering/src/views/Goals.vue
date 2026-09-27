<template>
  <div class="goals-page">
    <GoalTreeView
      :tree-data="goalTree"
      :loading="treeLoading"
      @refresh="loadGoalTree"
    />
  </div>
</template>

<script setup lang="ts">
// 目标对齐页：仅保留目标树（真实可用部分）。
// 原"目标详情/创建任务/对齐检查"三个 Tab 的子组件为空壳占位
// （<div><slot/></div>），已移除；待后端补齐后再按需实装。
import { ref, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { ElMessage } from "element-plus";
import http from "@/utils/http";
import GoalTreeView from "../components/goals/GoalTreeView.vue";
import { API_CONFIG, buildApiPath } from "@/config/api";

const { t } = useI18n();

interface GoalNode {
  id: string;
  name: string;
  level: string;
  status: string;
  children?: GoalNode[];
}

const goalTree = ref<GoalNode[]>([]);
const treeLoading = ref(false);

const loadGoalTree = async () => {
  treeLoading.value = true;
  try {
    const res = await http.get(
      buildApiPath(API_CONFIG.GOAL_ALIGNMENT, "/goals/tree"),
    );
    goalTree.value = res.data?.data || [];
  } catch (e: unknown) {
    const errorMsg = e instanceof Error ? e.message : String(e);
    ElMessage.error(t("goals.errorLoadTree") + errorMsg);
  } finally {
    treeLoading.value = false;
  }
};

onMounted(() => {
  loadGoalTree();
});
</script>

<style scoped>
.goals-page {
  padding: var(--spacing-md);
}
</style>
