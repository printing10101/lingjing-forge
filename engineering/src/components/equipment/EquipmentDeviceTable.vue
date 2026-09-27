<template>
  <div class="content-card">
    <div class="content-card__header">
      <span class="content-card__title">{{
        t("equipmentMonitor.sectionDeviceList")
      }}</span>
      <div class="filter-bar">
        <el-select
          v-model="statusFilter"
          :placeholder="t('equipmentMonitor.placeholderStatus')"
          size="small"
        >
          <el-option
            :label="t('equipmentMonitor.labelStatusAll')"
            value="all"
          />
          <el-option
            :label="t('equipmentMonitor.labelStatusRunning')"
            :value="t('equipmentMonitor.labelStatusRunning')"
          />
          <el-option
            :label="t('equipmentMonitor.labelStatusStandby')"
            :value="t('equipmentMonitor.labelStatusStandby')"
          />
          <el-option
            :label="t('equipmentMonitor.labelStatusFault')"
            :value="t('equipmentMonitor.labelStatusFault')"
          />
          <el-option
            :label="t('equipmentMonitor.labelStatusMaintenance')"
            :value="t('equipmentMonitor.labelStatusMaintenance')"
          />
        </el-select>
        <el-input
          v-model="searchKeyword"
          :placeholder="t('equipmentMonitor.placeholderSearch')"
          clearable
          size="small"
        />
      </div>
    </div>
    <div class="content-card__body">
      <el-table
        v-loading="loading"
        :data="filteredDevices"
        stripe
        style="width: 100%"
        :empty-text="t('equipmentMonitor.emptyData')"
      >
        <el-table-column
          prop="id"
          :label="t('equipmentMonitor.colDeviceId')"
          width="120"
        />
        <el-table-column
          prop="name"
          :label="t('equipmentMonitor.colDeviceName')"
          min-width="160"
        />
        <el-table-column
          prop="model"
          :label="t('equipmentMonitor.colDeviceModel')"
          min-width="180"
          show-overflow-tooltip
        />
        <el-table-column
          prop="location"
          :label="t('equipmentMonitor.colDeviceLocation')"
          min-width="140"
          show-overflow-tooltip
        />
        <el-table-column :label="t('equipmentMonitor.colStatus')" width="100">
          <template #default="{ row }">
            <el-tag
              :type="statusTagType(row.status)"
              size="small"
              effect="light"
            >
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          prop="temperature"
          :label="t('equipmentMonitor.colTemperature')"
          width="110"
        />
        <el-table-column
          prop="vibration"
          :label="t('equipmentMonitor.colVibration')"
          width="120"
        />
        <el-table-column
          prop="rpm"
          :label="t('equipmentMonitor.colRpm')"
          width="120"
        />
        <el-table-column
          prop="power"
          :label="t('equipmentMonitor.colPower')"
          width="110"
        />
        <el-table-column
          :label="t('equipmentMonitor.colActions')"
          width="180"
          fixed="right"
        >
          <template #default="{ row }">
            <el-button
              text
              type="primary"
              size="small"
              @click="emit('viewDetail', row as Device)"
            >
              {{ t("equipmentMonitor.btnDetail") }}
            </el-button>
            <el-button
              text
              size="small"
              @click="emit('settings', row as Device)"
            >
              {{ t("equipmentMonitor.btnSettings") }}
            </el-button>
            <el-button
              v-if="row.status === t('equipmentMonitor.labelStatusRunning')"
              text
              type="warning"
              size="small"
              @click="emit('stop', row as Device)"
            >
              {{ t("equipmentMonitor.btnStop") }}
            </el-button>
            <el-button
              v-if="row.status === t('equipmentMonitor.labelStatusFault')"
              text
              type="danger"
              size="small"
              @click="emit('repair', row as Device)"
            >
              {{ t("equipmentMonitor.btnRepair") }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from "vue";
import { useI18n } from "vue-i18n";

const { t } = useI18n();

export interface Device {
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

const props = defineProps<{
  devices: Device[];
  loading: boolean;
}>();

const emit = defineEmits<{
  (e: "viewDetail", device: Device): void;
  (e: "settings", device: Device): void;
  (e: "stop", device: Device): void;
  (e: "repair", device: Device): void;
}>();

// 内部筛选状态
const statusFilter = ref("all");
const searchKeyword = ref("");

// 状态映射
const STATUS_TO_TAG_TYPE: Record<
  string,
  "success" | "info" | "danger" | "warning"
> = {
  [t("equipmentMonitor.labelStatusRunning")]: "success",
  [t("equipmentMonitor.labelStatusStandby")]: "info",
  [t("equipmentMonitor.labelStatusFault")]: "danger",
  [t("equipmentMonitor.labelStatusMaintenance")]: "warning",
};

// 计算属性
const filteredDevices = computed(() => {
  return props.devices.filter((d) => {
    const keyword = searchKeyword.value.trim().toLowerCase();
    if (
      keyword &&
      !String(d.id).includes(keyword) &&
      !d.name.toLowerCase().includes(keyword)
    ) {
      return false;
    }
    if (statusFilter.value !== "all" && d.status !== statusFilter.value) {
      return false;
    }
    return true;
  });
});

// 方法
function statusTagType(
  status: string,
): "success" | "info" | "danger" | "warning" {
  return STATUS_TO_TAG_TYPE[status] || "info";
}
</script>
