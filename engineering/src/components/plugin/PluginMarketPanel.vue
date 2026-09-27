<template>
  <div class="market-panel">
    <div class="panel-toolbar">
      <el-button size="small" :icon="Refresh" @click="fetchMarketplace">
        {{ t("equipmentMonitor.btnRefresh") }}
      </el-button>
      <el-input
        v-model="searchQuery"
        :placeholder="t('pluginMarket.placeholderSearch')"
        style="width: 300px"
        clearable
      >
        <template #prefix>
          <el-icon><Search /></el-icon>
        </template>
      </el-input>
    </div>

    <el-row v-loading="loading" :gutter="16" style="margin-top: 4px">
      <el-col
        v-for="plugin in filteredPlugins"
        :key="plugin.id"
        :xs="24"
        :sm="12"
        :md="8"
        :lg="6"
      >
        <el-card class="plugin-card" shadow="hover">
          <div class="plugin-icon">
            <el-icon :size="48">
              <Setting />
            </el-icon>
          </div>
          <h3 class="plugin-name">
            {{ plugin.name }}
          </h3>
          <p class="plugin-desc">
            {{ plugin.description }}
          </p>
          <div class="plugin-meta">
            <el-tag size="small">
              {{ plugin.plugin_type }}
            </el-tag>
            <span class="version">v{{ plugin.version }}</span>
            <el-tag
              v-if="plugin.installed"
              size="small"
              type="success"
              effect="light"
            >
              {{ t("pluginMarket.labelInstalled") }}
            </el-tag>
          </div>
          <div class="plugin-actions">
            <el-button
              v-if="!plugin.installed"
              type="primary"
              size="small"
              :loading="installingId === plugin.id"
              @click="handleInstall(plugin)"
            >
              {{ t("pluginMarket.btnInstall") }}
            </el-button>
            <el-button v-else type="success" size="small" disabled>
              {{ t("pluginMarket.labelInstalled") }}
            </el-button>
            <el-button size="small" @click="handleViewDetail(plugin)">
              {{ t("pluginMarket.btnDetail") }}
            </el-button>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-empty
      v-if="!loading && filteredPlugins.length === 0"
      :description="
        loadError
          ? t('pluginMarket.msgLoadFailed')
          : t('pluginMarket.emptyNoPlugin')
      "
    />

    <!-- 插件详情弹窗 -->
    <el-dialog
      v-model="detailDialogVisible"
      :title="t('pluginMarket.dialogDetailTitle')"
      width="520px"
    >
      <div v-loading="detailLoading" style="min-height: 160px">
        <template v-if="detailData">
          <el-descriptions :column="1" border>
            <el-descriptions-item :label="t('pluginMarket.fieldType')">
              {{ detailData.plugin_type || "—" }}
            </el-descriptions-item>
            <el-descriptions-item :label="t('pluginMarket.fieldVersion')">
              {{ detailData.version || "—" }}
            </el-descriptions-item>
            <el-descriptions-item :label="t('pluginMarket.fieldAuthor')">
              {{ detailData.author || "—" }}
            </el-descriptions-item>
            <el-descriptions-item :label="t('pluginMarket.fieldStatus')">
              <template v-if="detailData.status">
                {{
                  detailData.status === "enabled"
                    ? t("pluginMarket.statusEnabled")
                    : detailData.status
                }}
              </template>
              <template v-else> — </template>
            </el-descriptions-item>
            <el-descriptions-item :label="t('pluginMarket.fieldDescription')">
              {{ detailData.description || "—" }}
            </el-descriptions-item>
            <el-descriptions-item :label="t('pluginMarket.fieldEntryPoint')">
              {{ detailData.entry_point || "—" }}
            </el-descriptions-item>
          </el-descriptions>
        </template>
      </div>
      <template #footer>
        <el-button @click="detailDialogVisible = false">
          {{ t("pluginMarket.btnClose") }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
// 原 /plugin-market 页面内容，现挂载于插件中心"市场"Tab
import { ref, computed, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { ElMessage } from "element-plus";
import { Search, Setting, Refresh } from "@element-plus/icons-vue";
import http from "@/utils/http";
import { API_CONFIG } from "@/config/api";

const { t } = useI18n();

interface MarketplacePlugin {
  id: string;
  name: string;
  version: string;
  description: string;
  plugin_type: string;
  author: string;
  entry_point?: string;
  status?: string | null;
  installed?: boolean;
}

const searchQuery = ref("");
const marketplacePlugins = ref<MarketplacePlugin[]>([]);
const loading = ref(false);
const loadError = ref(false);
const installingId = ref("");

// 详情弹窗
const detailDialogVisible = ref(false);
const detailLoading = ref(false);
const detailData = ref<MarketplacePlugin | null>(null);

const filteredPlugins = computed(() => {
  if (!searchQuery.value) return marketplacePlugins.value;
  const query = searchQuery.value.toLowerCase();
  return marketplacePlugins.value.filter(
    (p) =>
      p.name.toLowerCase().includes(query) ||
      p.description.toLowerCase().includes(query) ||
      p.plugin_type.toLowerCase().includes(query),
  );
});

/** 从后端拉取真实市场插件列表（GET /api/v1/plugins/marketplace）。 */
async function fetchMarketplace() {
  loading.value = true;
  loadError.value = false;
  try {
    const res = await http.get(API_CONFIG.PLUGINS + "/marketplace");
    if (res.data.code === 0 && res.data.data) {
      marketplacePlugins.value = res.data.data.plugins ?? [];
    } else {
      marketplacePlugins.value = [];
      loadError.value = true;
    }
  } catch (e: unknown) {
    console.warn("[PluginCenter] fetch marketplace failed:", e);
    marketplacePlugins.value = [];
    loadError.value = true;
  } finally {
    loading.value = false;
  }
}

/** 真实安装插件（POST /api/v1/plugins/marketplace/{id}/install）。 */
async function handleInstall(plugin: MarketplacePlugin) {
  if (plugin.installed) {
    ElMessage.info(
      t("pluginMarket.msgAlreadyInstalled", { name: plugin.name }),
    );
    return;
  }
  installingId.value = plugin.id;
  try {
    const res = await http.post(
      API_CONFIG.PLUGINS + `/marketplace/${plugin.id}/install`,
    );
    if (res.data.code === 0) {
      ElMessage.success(
        t("pluginMarket.msgInstallSuccess", { name: plugin.name }),
      );
      await fetchMarketplace();
    } else {
      ElMessage.error(res.data.message || t("pluginMarket.msgInstallFailed"));
    }
  } catch (e: unknown) {
    console.warn("[PluginCenter] install failed:", e);
    ElMessage.error(t("pluginMarket.msgInstallFailed"));
  } finally {
    installingId.value = "";
  }
}

/** 查看插件详情：优先拉取已注册插件详情，未注册则展示市场条目信息。 */
async function handleViewDetail(plugin: MarketplacePlugin) {
  detailDialogVisible.value = true;
  detailLoading.value = true;
  detailData.value = null;
  try {
    const res = await http.get(API_CONFIG.PLUGINS + `/${plugin.id}`);
    if (res.data.code === 0 && res.data.data) {
      detailData.value = { ...plugin, ...res.data.data };
    } else {
      detailData.value = plugin;
    }
  } catch (e: unknown) {
    console.warn("[PluginCenter] fetch detail failed:", e);
    // 未注册插件（如内置包）无详情接口，展示市场条目数据
    detailData.value = plugin;
  } finally {
    detailLoading.value = false;
  }
}

onMounted(() => {
  fetchMarketplace();
});
</script>

<style scoped>
.panel-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.plugin-card {
  text-align: center;
  margin-bottom: 16px;
}
.plugin-icon {
  color: var(--accent-primary);
  margin-bottom: 10px;
}
.plugin-name {
  margin: 10px 0 5px;
  font-size: 16px;
}
.plugin-desc {
  color: var(--text-tertiary);
  font-size: 13px;
  margin: 5px 0 15px;
  min-height: 40px;
}
.plugin-meta {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 10px;
  margin-bottom: 15px;
}
.version {
  color: var(--text-secondary);
  font-size: 12px;
}
.plugin-actions {
  display: flex;
  justify-content: center;
  gap: 10px;
}
</style>
