<template>
  <div class="material-management-page">
    <!-- 页面头部 -->
    <div class="page-header">
      <div class="page-header__title">
        <h1 class="page-title">
          {{ t("materialManagement.pageTitle") }}
        </h1>
        <p class="page-subtitle">
          {{ t("materialManagement.pageSubtitle") }}
        </p>
      </div>
      <div class="page-header__actions">
        <el-button
          type="primary"
          size="small"
          :icon="Plus"
          @click="() => handleStockIn()"
        >
          {{ t("materialManagement.btnStockIn") }}
        </el-button>
      </div>
    </div>

    <!-- 统计卡片 -->
    <MaterialStatsCards :cards="statsCards" />

    <!-- 物料列表 -->
    <div class="content-card">
      <div class="content-card__header">
        <span class="content-card__title">{{
          t("materialManagement.sectionMaterialList")
        }}</span>
        <div style="display: flex; gap: 8px">
          <el-select
            v-model="statusFilter"
            :placeholder="t('materialManagement.placeholderStatus')"
            size="small"
            style="width: 130px"
            @change="fetchMaterials"
          >
            <el-option
              :label="t('materialManagement.labelStatusAll')"
              value="all"
            />
            <el-option
              :label="t('materialManagement.labelStatusNormal')"
              :value="t('materialManagement.labelStatusNormal')"
            />
            <el-option
              :label="t('materialManagement.labelStatusLow')"
              :value="t('materialManagement.labelStatusLow')"
            />
            <el-option
              :label="t('materialManagement.labelStatusOut')"
              :value="t('materialManagement.labelStatusOut')"
            />
          </el-select>
          <el-select
            v-model="categoryFilter"
            :placeholder="t('materialManagement.placeholderCategory')"
            size="small"
            style="width: 130px"
            @change="fetchMaterials"
          >
            <el-option
              :label="t('materialManagement.labelCategoryAll')"
              value="all"
            />
            <el-option
              :label="t('materialManagement.labelCategoryRaw')"
              :value="t('materialManagement.labelCategoryRaw')"
            />
            <el-option
              :label="t('materialManagement.labelCategorySemi')"
              :value="t('materialManagement.labelCategorySemi')"
            />
            <el-option
              :label="t('materialManagement.labelCategoryFinished')"
              :value="t('materialManagement.labelCategoryFinished')"
            />
          </el-select>
          <el-input
            v-model="searchKeyword"
            :placeholder="t('materialManagement.placeholderSearch')"
            clearable
            size="small"
            style="width: 180px"
            @clear="fetchMaterials"
            @keyup.enter="fetchMaterials"
          />
        </div>
      </div>
      <div class="content-card__body">
        <MaterialListTable
          :materials="materials"
          :loading="loading"
          :load-error="loadError"
          @view="handleViewDetail"
          @stock-in="handleStockIn"
          @purchase="handlePurchase"
        />
      </div>
    </div>

    <!-- 入库登记弹窗 -->
    <MaterialStockInDialog
      v-model:visible="stockInDialogVisible"
      :materials="materials"
      :submitting="stockInSubmitting"
      :initial-material-id="preselectedMaterialId"
      @submit="submitStockIn"
    />

    <!-- 采购申请弹窗 -->
    <MaterialPurchaseDialog
      v-model:visible="purchaseDialogVisible"
      :material-label="purchaseMaterialLabel"
      :submitting="purchaseSubmitting"
      :initial-material-id="purchaseMaterialId"
      :initial-supplier="purchaseSupplier"
      @submit="submitPurchase"
    />

    <!-- 物料详情弹窗 -->
    <MaterialDetailDialog
      v-model:visible="detailDialogVisible"
      :loading="detailLoading"
      :data="detailData"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, type Component } from "vue";
import { useI18n } from "vue-i18n";
import { ElMessage } from "element-plus";
import { Plus, Box, Warning, CircleClose } from "@element-plus/icons-vue";
import http from "@/utils/http";
import { API_CONFIG } from "@/config/api";

import MaterialStatsCards from "@/components/material/MaterialStatsCards.vue";
import MaterialStockInDialog from "@/components/material/MaterialStockInDialog.vue";
import MaterialPurchaseDialog from "@/components/material/MaterialPurchaseDialog.vue";
import MaterialDetailDialog from "@/components/material/MaterialDetailDialog.vue";
import MaterialListTable from "@/components/material/MaterialListTable.vue";

const { t } = useI18n();

// 类型定义
interface Material {
  id: number;
  code: string;
  name: string;
  spec: string;
  category: string;
  quantity: number;
  safe_quantity: number;
  status: string;
  location: string;
  unit: string;
  supplier: string;
  created_at: string;
  updated_at: string;
}

interface StatsSummary {
  total: number;
  low_stock: number;
  out_of_stock: number;
}

// 状态
const loading = ref(false);
const loadError = ref(false);
const searchKeyword = ref("");
const statusFilter = ref("all");
const categoryFilter = ref("all");

const materials = ref<Material[]>([]);
const statsSummary = ref<StatsSummary>({
  total: 0,
  low_stock: 0,
  out_of_stock: 0,
});

// 计算属性
const statsCards = computed(() => {
  return [
    {
      label: t("materialManagement.statTotal"),
      value: statsSummary.value.total,
      icon: Box as Component,
      type: "default" as const,
    },
    {
      label: t("materialManagement.statLowStock"),
      value: statsSummary.value.low_stock,
      icon: Warning as Component,
      type: "warning" as const,
    },
    {
      label: t("materialManagement.statOutOfStock"),
      value: statsSummary.value.out_of_stock,
      icon: CircleClose as Component,
      type: "danger" as const,
    },
  ];
});

// 方法
async function fetchMaterials() {
  loading.value = true;
  loadError.value = false;
  try {
    const params: Record<string, string> = {};
    if (statusFilter.value !== "all") params.status = statusFilter.value;
    if (categoryFilter.value !== "all") params.category = categoryFilter.value;
    const keyword = searchKeyword.value.trim();
    if (keyword) params.keyword = keyword;

    const [materialsRes, statsRes] = await Promise.all([
      http.get(API_CONFIG.MATERIALS + "/", { params }),
      http.get(API_CONFIG.MATERIALS + "/stats/summary"),
    ]);

    // 后端列表返回 { items, total, page, page_size, total_pages }
    materials.value = materialsRes.data?.data?.items || [];
    statsSummary.value = statsRes.data?.data || {
      total: 0,
      low_stock: 0,
      out_of_stock: 0,
    };
  } catch {
    loadError.value = true;
    materials.value = [];
    statsSummary.value = { total: 0, low_stock: 0, out_of_stock: 0 };
  } finally {
    loading.value = false;
  }
}

// 入库登记弹窗
const stockInDialogVisible = ref(false);
const stockInSubmitting = ref(false);
const preselectedMaterialId = ref<number | "">("");

/** 打开入库登记弹窗（顶部按钮不传物料，行内按钮预选物料）。 */
function handleStockIn(row?: Material) {
  if (row) {
    preselectedMaterialId.value = row.id;
  } else {
    preselectedMaterialId.value = "";
  }
  stockInDialogVisible.value = true;
}

interface StockInFormData {
  material_id: number | "";
  quantity: number;
  remark: string;
}

/** 提交入库登记。 */
async function submitStockIn(formData: StockInFormData) {
  if (!formData.material_id) {
    ElMessage.warning(t("materialManagement.msgMaterialRequired"));
    return;
  }
  if (!formData.quantity || formData.quantity <= 0) {
    ElMessage.warning(t("materialManagement.msgQuantityInvalid"));
    return;
  }
  stockInSubmitting.value = true;
  try {
    const res = await http.post(
      API_CONFIG.MATERIALS + `/${formData.material_id}/stock-in`,
      {
        quantity: formData.quantity,
        remark: formData.remark.trim() || null,
      },
    );
    if (res.data.code === 0) {
      ElMessage.success(t("materialManagement.msgStockInSuccess"));
      stockInDialogVisible.value = false;
      fetchMaterials();
    } else {
      ElMessage.error(
        res.data.message || t("materialManagement.msgOperationFailed"),
      );
    }
  } catch (e: unknown) {
    console.warn("[MaterialManagement] stock-in failed:", e);
    ElMessage.error(t("materialManagement.msgOperationFailed"));
  } finally {
    stockInSubmitting.value = false;
  }
}

// 采购申请弹窗
const purchaseDialogVisible = ref(false);
const purchaseSubmitting = ref(false);
const purchaseMaterialId = ref<number | "">("");
const purchaseSupplier = ref("");

/** 打开采购申请弹窗。 */
function handlePurchase(row: Material) {
  purchaseMaterialId.value = row.id;
  purchaseSupplier.value = row.supplier || "";
  purchaseDialogVisible.value = true;
}

interface PurchaseFormData {
  material_id: number | "";
  quantity: number;
  supplier: string;
}

/** 提交采购申请。 */
async function submitPurchase(formData: PurchaseFormData) {
  if (!formData.material_id) {
    ElMessage.warning(t("materialManagement.msgMaterialRequired"));
    return;
  }
  if (!formData.quantity || formData.quantity <= 0) {
    ElMessage.warning(t("materialManagement.msgQuantityInvalid"));
    return;
  }
  purchaseSubmitting.value = true;
  try {
    const res = await http.post(
      API_CONFIG.MATERIALS + `/${formData.material_id}/purchase`,
      {
        quantity: formData.quantity,
        supplier: formData.supplier.trim() || null,
      },
    );
    if (res.data.code === 0) {
      ElMessage.success(t("materialManagement.msgPurchaseSuccess"));
      purchaseDialogVisible.value = false;
      fetchMaterials();
    } else {
      ElMessage.error(
        res.data.message || t("materialManagement.msgOperationFailed"),
      );
    }
  } catch (e: unknown) {
    console.warn("[MaterialManagement] purchase failed:", e);
    ElMessage.error(t("materialManagement.msgOperationFailed"));
  } finally {
    purchaseSubmitting.value = false;
  }
}

// 详情弹窗
const detailDialogVisible = ref(false);
const detailLoading = ref(false);
const detailData = ref<Material | null>(null);

/** 查看物料详情（GET /api/v1/materials/{id}）。 */
async function handleViewDetail(row: Material) {
  detailDialogVisible.value = true;
  detailLoading.value = true;
  detailData.value = null;
  try {
    const res = await http.get(API_CONFIG.MATERIALS + `/${row.id}`);
    if (res.data.code === 0 && res.data.data) {
      detailData.value = res.data.data;
    } else {
      ElMessage.error(
        res.data.message || t("materialManagement.msgOperationFailed"),
      );
    }
  } catch (e: unknown) {
    console.warn("[MaterialManagement] fetch detail failed:", e);
    ElMessage.error(t("materialManagement.msgOperationFailed"));
  } finally {
    detailLoading.value = false;
  }
}

/** 采购弹窗中当前选中物料的展示标签。 */
const purchaseMaterialLabel = computed(() => {
  const m = materials.value.find((x) => x.id === purchaseMaterialId.value);
  return m ? `${m.code} - ${m.name}` : "";
});

// 生命周期
onMounted(() => {
  fetchMaterials();
});
</script>

<style scoped>
.material-management-page {
  padding: var(--page-padding);
  max-width: var(--content-max-width);
  margin: 0 auto;
}
</style>
