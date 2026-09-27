<template>
  <el-table v-loading="loading" :data="modelList" style="width: 100%">
    <el-table-column prop="name" :label="$t('workspace.modelListName')" />
    <el-table-column prop="model_type" :label="$t('workspace.modelListType')" />
    <el-table-column prop="version" :label="$t('workspace.modelListVersion')" />
    <el-table-column
      prop="input_features"
      :label="$t('workspace.modelListFeatures')"
    >
      <template #default="{ row }">
        {{ row.input_features?.join(", ") }}
      </template>
    </el-table-column>
  </el-table>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import http from "@/utils/http";
import { API_CONFIG, buildApiPath } from "@/config/api";

const loading = ref(false);

interface ModelInfo {
  name: string;
  model_type: string;
  version: string;
  input_features?: string[];
}
const modelList = ref<ModelInfo[]>([]);

async function fetchModels(): Promise<void> {
  loading.value = true;
  try {
    // 修复：模型列表应走 LNN 模型注册表端点（/api/v1/lnn/models），
    // 旧的 /api/v1/models 无对应后端路由，始终 404 并在页面顶部弹出 Not Found toast
    const res = await http.get(buildApiPath(API_CONFIG.LNN, "/models"));
    modelList.value = res.data?.data?.models ?? [];
  } catch {
    modelList.value = [];
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  void fetchModels();
});
</script>
