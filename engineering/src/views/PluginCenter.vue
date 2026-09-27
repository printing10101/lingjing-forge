<template>
  <div class="plugin-center-page">
    <!-- 页面头部 -->
    <div class="page-header">
      <div class="page-header__title">
        <h1>{{ t("pluginCenter.pageTitle") }}</h1>
        <p class="subtitle">
          {{ t("pluginCenter.pageSubtitle") }}
        </p>
      </div>
    </div>

    <el-tabs v-model="activeTab" class="center-tabs">
      <el-tab-pane name="installed">
        <template #label>
          {{ t("pluginCenter.tabInstalled") }}
        </template>
        <PluginInstalledPanel v-if="visitedTabs.installed" />
      </el-tab-pane>
      <el-tab-pane name="market">
        <template #label>
          {{ t("pluginCenter.tabMarket") }}
        </template>
        <PluginMarketPanel v-if="visitedTabs.market" />
      </el-tab-pane>
      <el-tab-pane name="logs">
        <template #label>
          {{ t("pluginCenter.tabLogs") }}
        </template>
        <PluginLogsPanel v-if="visitedTabs.logs" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
// 插件中心：原 /plugin-manager、/plugin-market、/plugin-logs 三个页面合一。
// 市场/日志 Tab 懒加载，避免进入页面即发全部请求。
import { ref, reactive, watch } from "vue";
import { useRoute } from "vue-router";
import { useI18n } from "vue-i18n";
import PluginInstalledPanel from "@/components/plugin/PluginInstalledPanel.vue";
import PluginMarketPanel from "@/components/plugin/PluginMarketPanel.vue";
import PluginLogsPanel from "@/components/plugin/PluginLogsPanel.vue";

const { t } = useI18n();
const route = useRoute();

const VALID_TABS = ["installed", "market", "logs"] as const;
type CenterTab = (typeof VALID_TABS)[number];

const activeTab = ref<CenterTab>(
  VALID_TABS.includes(route.query.tab as CenterTab)
    ? (route.query.tab as CenterTab)
    : "installed",
);

// Tab 内容首次激活才挂载（不依赖 el-tabs 的 lazy，生产与测试行为一致）
const visitedTabs = reactive<Record<CenterTab, boolean>>({
  installed: true,
  market: false,
  logs: false,
});
watch(activeTab, (tab) => {
  visitedTabs[tab] = true;
});

// 深链兼容：/plugin-market → /plugins?tab=market 等旧链接经 redirect 到达时切 Tab
watch(
  () => route.query.tab,
  (tab) => {
    if (route.path === "/plugins" && VALID_TABS.includes(tab as CenterTab)) {
      activeTab.value = tab as CenterTab;
    }
  },
);
</script>

<style scoped>
.plugin-center-page {
  padding: var(--page-padding);
  max-width: var(--content-max-width);
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.page-header__title h1 {
  margin: 0 0 4px;
}

.subtitle {
  margin: 0;
  color: var(--text-tertiary);
  font-size: 0.875rem;
}
</style>
