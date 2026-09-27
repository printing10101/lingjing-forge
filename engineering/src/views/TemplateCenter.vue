<template>
  <div class="template-center-page">
    <!-- 页面头部 -->
    <div class="page-header">
      <div class="page-header__title">
        <h1>{{ t("templateCenter.pageTitle") }}</h1>
        <p class="subtitle">
          {{ t("templateCenter.pageSubtitle") }}
        </p>
      </div>
    </div>

    <el-tabs v-model="activeTab" class="center-tabs">
      <!-- ===== Tab 1: 模板市场（列表/热门/订阅/发布） ===== -->
      <el-tab-pane name="market">
        <template #label>
          {{ t("templateCenter.tabMarket") }}
        </template>
        <TemplateMarketPanel
          v-if="visitedTabs.market"
          @open-detail="openDetail"
        />
      </el-tab-pane>

      <!-- ===== Tab 2: 分支管理（原 /branch-manager 并入） ===== -->
      <el-tab-pane name="branches">
        <template #label>
          {{ t("templateCenter.tabBranches") }}
        </template>
        <TemplateBranchPanel
          v-if="visitedTabs.branches"
          @open-detail="openDetail"
        />
      </el-tab-pane>

      <!-- ===== Tab 3: 更新中心（原 /update-center 并入） ===== -->
      <el-tab-pane name="updates">
        <template #label>
          {{ t("templateCenter.tabUpdates") }}
        </template>
        <TemplateUpdatePanel v-if="visitedTabs.updates" />
      </el-tab-pane>
    </el-tabs>

    <!-- ===== 模板详情抽屉（原 /template-detail/:id 独立页） ===== -->
    <TemplateDetailDrawer
      v-model:visible="detailVisible"
      :branch-id="detailBranchId"
    />
  </div>
</template>

<script setup lang="ts">
// 模板中心：原 /template-market、/branch-manager、/update-center、/template-detail 四页合一。
// 分支管理/更新中心 Tab 懒加载；详情统一走抽屉，?template=<id> 深链自动打开。
import { ref, reactive, onMounted, watch } from "vue";
import { useRoute } from "vue-router";
import { useI18n } from "vue-i18n";
import TemplateMarketPanel from "@/components/template/TemplateMarketPanel.vue";
import TemplateBranchPanel from "@/components/template/TemplateBranchPanel.vue";
import TemplateUpdatePanel from "@/components/template/TemplateUpdatePanel.vue";
import TemplateDetailDrawer from "@/components/template/TemplateDetailDrawer.vue";

const { t } = useI18n();
const route = useRoute();

const VALID_TABS = ["market", "branches", "updates"] as const;
type CenterTab = (typeof VALID_TABS)[number];

const activeTab = ref<CenterTab>(
  VALID_TABS.includes(route.query.tab as CenterTab)
    ? (route.query.tab as CenterTab)
    : "market",
);

// Tab 内容首次激活才挂载（不依赖 el-tabs 的 lazy，生产与测试行为一致）
const visitedTabs = reactive<Record<CenterTab, boolean>>({
  market: true,
  branches: false,
  updates: false,
});
watch(activeTab, (tab) => {
  visitedTabs[tab] = true;
});

const detailVisible = ref(false);
const detailBranchId = ref<string | null>(null);

function openDetail(branchId: string) {
  detailBranchId.value = branchId;
  detailVisible.value = true;
}

// 深链兼容：/branch-manager → ?tab=branches；/template-detail/:id → ?template=<id>
watch(
  () => route.query.tab,
  (tab) => {
    if (
      route.path === "/template-market" &&
      VALID_TABS.includes(tab as CenterTab)
    ) {
      activeTab.value = tab as CenterTab;
    }
  },
);

onMounted(() => {
  const tpl = route.query.template;
  if (typeof tpl === "string" && tpl) {
    openDetail(tpl);
  }
});
</script>

<style scoped>
.template-center-page {
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
