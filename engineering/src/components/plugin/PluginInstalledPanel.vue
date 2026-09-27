<template>
  <div class="installed-panel">
    <div class="panel-toolbar">
      <el-button type="primary" @click="refreshPlugins">
        <el-icon><Refresh /></el-icon> {{ t("pluginManager.btnRefresh") }}
      </el-button>
      <el-input
        v-model="searchQuery"
        :placeholder="t('pluginManager.searchPlaceholder')"
        style="width: 200px; margin-left: 10px"
        clearable
      >
        <template #prefix>
          <el-icon><Search /></el-icon>
        </template>
      </el-input>
    </div>

    <PluginStats
      :total="plugins.length"
      :enabled="enabledPlugins.length"
      :disabled="disabledPlugins.length"
      :error="errorPlugins.length"
    />

    <el-card class="plugins-card">
      <el-tabs v-model="activeTab">
        <el-tab-pane :label="t('pluginManager.tabAll')" name="all">
          <PluginTable
            :plugins="filteredPlugins"
            :show-type="true"
            @detail="handleDetail"
            @enable="handleEnable"
            @disable="handleDisable"
            @uninstall="handleUninstall"
          />
        </el-tab-pane>
        <el-tab-pane :label="t('pluginManager.tabAdapter')" name="adapter">
          <PluginTable
            :plugins="adapterPlugins"
            @detail="handleDetail"
            @enable="handleEnable"
            @disable="handleDisable"
            @uninstall="handleUninstall"
          />
        </el-tab-pane>
        <el-tab-pane
          :label="t('pluginManager.tabDataSource')"
          name="data_source"
        >
          <PluginTable
            :plugins="dataSourcePlugins"
            @detail="handleDetail"
            @enable="handleEnable"
            @disable="handleDisable"
            @uninstall="handleUninstall"
          />
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <PluginDetailDialog
      v-model:visible="detailDialogVisible"
      :plugin="currentPlugin"
    />
  </div>
</template>

<script setup lang="ts">
// 原 /plugin-manager 页面内容，现挂载于插件中心"已安装"Tab
import { ref, computed, onMounted } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useI18n } from "vue-i18n";
import { usePluginStore } from "../../stores/plugin";
import type { PluginDetail } from "../../stores/plugin";
import { Refresh, Search } from "@element-plus/icons-vue";
import PluginStats from "./PluginStats.vue";
import PluginTable from "./PluginTable.vue";
import PluginDetailDialog from "./PluginDetailDialog.vue";

const { t } = useI18n();
const pluginStore = usePluginStore();
const searchQuery = ref("");
const activeTab = ref("all");
const detailDialogVisible = ref(false);
const currentPlugin = ref<PluginDetail | null>(null);

const plugins = computed(() => pluginStore.plugins);
const enabledPlugins = computed(() => pluginStore.enabledPlugins);
const disabledPlugins = computed(() => pluginStore.disabledPlugins);
const adapterPlugins = computed(() => pluginStore.adapterPlugins);
const dataSourcePlugins = computed(() => pluginStore.dataSourcePlugins);

const errorPlugins = computed(() =>
  plugins.value.filter((p) => p.status === "error"),
);

const filteredPlugins = computed(() => {
  if (!searchQuery.value) return plugins.value;
  const query = searchQuery.value.toLowerCase();
  return plugins.value.filter(
    (p) =>
      p.name.toLowerCase().includes(query) ||
      p.id.toLowerCase().includes(query) ||
      p.description.toLowerCase().includes(query),
  );
});

onMounted(() => {
  pluginStore.fetchPlugins();
});

const refreshPlugins = () => {
  pluginStore.fetchPlugins();
  ElMessage.success(t("pluginManager.msgRefreshed"));
};

const handleEnable = async (pluginId: string) => {
  await pluginStore.enablePlugin(pluginId);
  ElMessage.success(t("pluginManager.msgEnabled"));
};

const handleDisable = async (pluginId: string) => {
  await pluginStore.disablePlugin(pluginId);
  ElMessage.success(t("pluginManager.msgDisabled"));
};

const handleUninstall = async (pluginId: string) => {
  try {
    await ElMessageBox.confirm(
      t("pluginManager.msgUninstallConfirm"),
      t("pluginManager.msgUninstallConfirmTitle"),
      {
        confirmButtonText: t("pluginManager.btnUninstallConfirm"),
        cancelButtonText: t("pluginManager.btnCancel"),
        type: "warning",
      },
    );
    await pluginStore.uninstallPlugin(pluginId);
    ElMessage.success(t("pluginManager.msgUninstalled"));
  } catch (e: unknown) {
    const cancelled =
      e === "cancel" || (e instanceof Error && e.message.includes("cancel"));
    if (cancelled) return;
    console.error("[PluginCenter] uninstallPlugin failed:", e);
    ElMessage.error(
      t("pluginManager.msgUninstallFailed") || "卸载插件失败，请稍后重试",
    );
  }
};

const handleDetail = async (pluginId: string) => {
  await pluginStore.fetchPluginDetail(pluginId);
  currentPlugin.value = pluginStore.currentPlugin;
  detailDialogVisible.value = true;
};
</script>

<style scoped>
.panel-toolbar {
  display: flex;
  align-items: center;
  margin-bottom: 16px;
}

.plugins-card {
  min-height: 400px;
}
</style>
