<template>
  <div class="equipment-monitor-page">
    <!-- 页面头部 -->
    <div class="page-header">
      <div class="page-header__title">
        <h1 class="page-title">
          {{ t("equipmentMonitor.pageTitle") }}
        </h1>
        <p class="page-subtitle">
          {{ t("equipmentMonitor.pageSubtitle") }}
        </p>
      </div>
      <div class="page-header__actions">
        <el-button
          size="small"
          :icon="Refresh"
          :loading="refreshing"
          @click="handleRefresh"
        >
          {{ t("equipmentMonitor.btnRefresh") }}
        </el-button>
      </div>
    </div>

    <!-- 统计卡片 -->
    <EquipmentStatsCards :cards="statsCards" />

    <!-- 设备列表 -->
    <EquipmentDeviceTable
      :devices="devices"
      :loading="loading"
      @view-detail="handleViewDetail"
      @settings="handleSettings"
      @stop="handleStop"
      @repair="handleRepair"
    />

    <!-- 设备参数设置弹窗 -->
    <EquipmentSettingsDialog
      v-model:visible="settingsDialogVisible"
      :form="settingsForm"
      :submitting="settingsSubmitting"
      @save="submitSettings"
      @update:form="settingsForm = $event"
    />

    <!-- 设备详情弹窗 -->
    <EquipmentDetailDialog
      v-model:visible="detailDialogVisible"
      :loading="detailLoading"
      :device="detailData"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, type Component } from "vue";
import { useI18n } from "vue-i18n";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  Refresh,
  Monitor,
  VideoPlay,
  Clock,
  WarningFilled,
} from "@element-plus/icons-vue";
import http from "@/utils/http";
import { API_CONFIG } from "@/config/api";
import EquipmentStatsCards from "@/components/equipment/EquipmentStatsCards.vue";
import EquipmentDeviceTable from "@/components/equipment/EquipmentDeviceTable.vue";
import EquipmentSettingsDialog from "@/components/equipment/EquipmentSettingsDialog.vue";
import EquipmentDetailDialog from "@/components/equipment/EquipmentDetailDialog.vue";

const { t } = useI18n();

// 类型定义
interface Device {
  id: number;
  name: string;
  model: string;
  location: string;
  status: string;
  temperature: number | null;
  vibration: number | null;
  rpm: number | null;
  power: number | null;
  created_at: string;
  updated_at: string;
}

interface EquipmentStats {
  total: number;
  running: number;
  standby: number;
  maintenance: number;
  fault: number;
}

interface StatsCard {
  label: string;
  value: number;
  icon: Component;
  type: string;
}

// 状态
const loading = ref(false);
const refreshing = ref(false);

const devices = ref<Device[]>([]);
const stats = ref<EquipmentStats>({
  total: 0,
  running: 0,
  standby: 0,
  maintenance: 0,
  fault: 0,
});

// 计算属性
const statsCards = computed<StatsCard[]>(() => {
  const s = stats.value;
  return [
    {
      label: t("equipmentMonitor.statTotal"),
      value: s.total,
      icon: Monitor,
      type: "default",
    },
    {
      label: t("equipmentMonitor.statRunning"),
      value: s.running,
      icon: VideoPlay,
      type: "success",
    },
    {
      label: t("equipmentMonitor.statStandby"),
      value: s.standby,
      icon: Clock,
      type: "info",
    },
    {
      label: t("equipmentMonitor.statFault"),
      value: s.fault,
      icon: WarningFilled,
      type: "danger",
    },
  ];
});

// 数据获取
async function fetchDevices() {
  loading.value = true;
  try {
    const [deviceRes, statsRes] = await Promise.all([
      http.get(API_CONFIG.EQUIPMENT),
      http.get(API_CONFIG.EQUIPMENT + "/stats/"),
    ]);

    if (deviceRes.data?.code === 0) {
      devices.value = deviceRes.data.data?.items || [];
    } else {
      devices.value = [];
    }

    if (statsRes.data?.code === 0) {
      stats.value = statsRes.data.data || {
        total: 0,
        running: 0,
        standby: 0,
        maintenance: 0,
        fault: 0,
      };
    }
  } catch {
    devices.value = [];
    stats.value = {
      total: 0,
      running: 0,
      standby: 0,
      maintenance: 0,
      fault: 0,
    };
    ElMessage.error(t("equipmentMonitor.msgLoadFailed"));
  } finally {
    loading.value = false;
  }
}

async function handleRefresh() {
  refreshing.value = true;
  await fetchDevices();
  refreshing.value = false;
  ElMessage.success(t("equipmentMonitor.msgRefreshed"));
}

// 设备参数设置弹窗
const settingsDialogVisible = ref(false);
const settingsSubmitting = ref(false);
const settingsForm = ref({
  id: 0,
  name: "",
  status: "",
  temperature: null as number | null,
  vibration: null as number | null,
  rpm: null as number | null,
  power: null as number | null,
});

function handleSettings(device: Device) {
  settingsForm.value = {
    id: device.id,
    name: device.name,
    status: device.status,
    temperature: device.temperature,
    vibration: device.vibration,
    rpm: device.rpm,
    power: device.power,
  };
  settingsDialogVisible.value = true;
}

async function submitSettings() {
  if (!settingsForm.value.id) return;
  settingsSubmitting.value = true;
  try {
    const res = await http.put(
      API_CONFIG.EQUIPMENT + `/${settingsForm.value.id}`,
      {
        status: settingsForm.value.status,
        temperature: settingsForm.value.temperature,
        vibration: settingsForm.value.vibration,
        rpm: settingsForm.value.rpm,
        power: settingsForm.value.power,
      },
    );
    if (res.data.code === 0) {
      ElMessage.success(t("equipmentMonitor.msgSettingsSaved"));
      settingsDialogVisible.value = false;
      fetchDevices();
    } else {
      ElMessage.error(
        res.data.message || t("equipmentMonitor.msgSettingsFailed"),
      );
    }
  } catch (e: unknown) {
    console.warn("[EquipmentMonitor] save settings failed:", e);
    ElMessage.error(t("equipmentMonitor.msgSettingsFailed"));
  } finally {
    settingsSubmitting.value = false;
  }
}

// 设备详情弹窗
const detailDialogVisible = ref(false);
const detailLoading = ref(false);
const detailData = ref<Device | null>(null);

async function handleViewDetail(row: Device) {
  detailDialogVisible.value = true;
  detailLoading.value = true;
  detailData.value = null;
  try {
    const res = await http.get(API_CONFIG.EQUIPMENT + `/${row.id}`);
    if (res.data.code === 0 && res.data.data) {
      detailData.value = res.data.data;
    } else {
      ElMessage.error(res.data.message || t("equipmentMonitor.msgOpFailed"));
    }
  } catch (e: unknown) {
    console.warn("[EquipmentMonitor] fetch detail failed:", e);
    ElMessage.error(t("equipmentMonitor.msgOpFailed"));
  } finally {
    detailLoading.value = false;
  }
}

// 停机 / 报修
async function handleStop(row: Device) {
  try {
    await ElMessageBox.confirm(
      t("equipmentMonitor.msgStopConfirm", { name: row.name }),
      t("equipmentMonitor.btnStop"),
      {
        type: "warning",
        confirmButtonText: t("equipmentMonitor.btnSubmit"),
        cancelButtonText: t("equipmentMonitor.btnCancel"),
      },
    );
  } catch {
    return;
  }
  try {
    const res = await http.put(API_CONFIG.EQUIPMENT + `/${row.id}`, {
      status: t("equipmentMonitor.labelStatusStandby"),
    });
    if (res.data.code === 0) {
      ElMessage.success(t("equipmentMonitor.msgStopSuccess"));
      fetchDevices();
    } else {
      ElMessage.error(res.data.message || t("equipmentMonitor.msgOpFailed"));
    }
  } catch (e: unknown) {
    console.warn("[EquipmentMonitor] stop failed:", e);
    ElMessage.error(t("equipmentMonitor.msgOpFailed"));
  }
}

async function handleRepair(row: Device) {
  try {
    await ElMessageBox.confirm(
      t("equipmentMonitor.msgRepairConfirm", { name: row.name }),
      t("equipmentMonitor.btnRepair"),
      {
        type: "warning",
        confirmButtonText: t("equipmentMonitor.btnSubmit"),
        cancelButtonText: t("equipmentMonitor.btnCancel"),
      },
    );
  } catch {
    return;
  }
  try {
    const res = await http.put(API_CONFIG.EQUIPMENT + `/${row.id}`, {
      status: t("equipmentMonitor.labelStatusMaintenance"),
    });
    if (res.data.code === 0) {
      ElMessage.success(t("equipmentMonitor.msgRepairSuccess"));
      fetchDevices();
    } else {
      ElMessage.error(res.data.message || t("equipmentMonitor.msgOpFailed"));
    }
  } catch (e: unknown) {
    console.warn("[EquipmentMonitor] repair failed:", e);
    ElMessage.error(t("equipmentMonitor.msgOpFailed"));
  }
}

// 生命周期
onMounted(() => {
  fetchDevices();
});
</script>

<style scoped>
.equipment-monitor-page {
  padding: var(--page-padding);
  max-width: var(--content-max-width);
  margin: 0 auto;
}

.runtime-text {
  font-variant-numeric: tabular-nums;
  color: var(--text-accent);
  font-weight: 500;
}
</style>
