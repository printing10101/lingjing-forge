<script setup lang="ts">
// 原 /template-market 页面内容，现挂载于模板中心"模板市场"Tab。
// 修复：原"模板列表"Tab 的 templates ref 从未请求赋值（死 UI），现从
// /templates/branches/ 拉取真实分支列表。
import { ref, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import http from "@/utils/http";
import { API_CONFIG, buildApiPath } from "@/config/api";
import { extractErrorMessage } from "@/utils/error-handler";
import { useProjectStore } from "@/stores/project";

const { t } = useI18n();
const emit = defineEmits<{
  "open-detail": [branchId: string];
  "switch-tab": [tab: string];
}>();

const projectStore = useProjectStore();

// 类型定义
interface TrendingTemplate {
  template_id: string;
  name: string;
  category: string;
  downloads: number;
  rating: number;
}

interface Template {
  branch_id: string;
  name: string;
  category: string;
  description: string;
  version: string;
  author: string;
  created_at: number;
  updated_at: number;
  metadata?: Record<string, unknown>;
  commit_log?: Array<Record<string, unknown>>;
}

const trending = ref<TrendingTemplate[]>([]);
const templates = ref<Template[]>([]);
const subscriptions = ref<string[]>([]);
const activeTab = ref("list");
const loading = ref(false);
const subscribeCategory = ref("");
const publishForm = ref({
  branch_id: "",
  name: "",
  category: "general",
  description: "",
});

// 缓存机制：避免重复请求
const trendingCache = ref<TrendingTemplate[] | null>(null);
const trendingCacheTime = ref<number>(0);
const CACHE_DURATION = 60000; // 1分钟缓存

async function fetchTemplates() {
  loading.value = true;
  try {
    const res = await http.get(
      buildApiPath(API_CONFIG.V1, "/templates/branches/"),
    );
    if (res.data.code === "SUCCESS") templates.value = res.data.data;
  } catch (error) {
    console.warn(
      "[TemplateCenter] fetchTemplates failed:",
      extractErrorMessage(error),
    );
  } finally {
    loading.value = false;
  }
}

async function fetchTrending(forceRefresh = false) {
  const now = Date.now();

  // 使用缓存（除非强制刷新）
  if (
    !forceRefresh &&
    trendingCache.value &&
    now - trendingCacheTime.value < CACHE_DURATION
  ) {
    trending.value = trendingCache.value;
    return;
  }

  try {
    const res = await http.get(
      buildApiPath(API_CONFIG.V1, "/template_market/trending"),
    );
    if (res.data.code === "SUCCESS") {
      trending.value = res.data.data;
      trendingCache.value = res.data.data;
      trendingCacheTime.value = now;
    }
  } catch (error) {
    console.warn(
      t("templateMarket.errorFetchTrending"),
      extractErrorMessage(error),
    );
  }
}

async function subscribe() {
  if (!subscribeCategory.value.trim()) return;
  try {
    await http.post(buildApiPath(API_CONFIG.V1, "/template_market/subscribe"), {
      project_id: projectStore.projectId || "default",
      category: subscribeCategory.value,
    });
    subscriptions.value.push(subscribeCategory.value);
    subscribeCategory.value = "";
  } catch (error) {
    console.warn(
      t("templateMarket.errorSubscribe"),
      extractErrorMessage(error),
    );
  }
}

async function publishTemplate() {
  if (!publishForm.value.branch_id || !publishForm.value.name) return;
  try {
    await http.post(
      buildApiPath(API_CONFIG.V1, "/template_market/publish"),
      publishForm.value,
    );
    publishForm.value = {
      branch_id: "",
      name: "",
      category: "general",
      description: "",
    };
    fetchTrending(true); // 发布后强制刷新缓存
  } catch (error) {
    console.warn(t("templateMarket.errorPublish"), extractErrorMessage(error));
  }
}

onMounted(() => {
  fetchTemplates();
  fetchTrending();
});
</script>

<template>
  <div class="market-panel">
    <el-tabs v-model="activeTab" class="main-tabs">
      <el-tab-pane :label="t('templateMarket.tabTemplateList')" name="list">
        <div v-if="loading" class="loading">
          {{ t("common.loading") }}
        </div>
        <el-row v-else :gutter="16">
          <el-col v-for="tpl in templates" :key="tpl.branch_id" :span="8">
            <el-card
              shadow="hover"
              class="template-card"
              @click="emit('open-detail', tpl.branch_id)"
            >
              <template #header>
                <div class="card-header">
                  <span class="template-name">{{ tpl.name }}</span>
                  <el-tag
                    size="small"
                    :type="tpl.metadata?.type === 'main' ? 'success' : 'info'"
                  >
                    {{ tpl.metadata?.type || "unknown" }}
                  </el-tag>
                </div>
              </template>
              <div class="template-meta">
                <div>
                  {{ t("templateMarket.labelUpdateTime") }}:
                  {{ new Date(tpl.updated_at * 1000).toLocaleDateString() }}
                </div>
                <div>
                  {{ t("templateMarket.labelCommitLog") }}:
                  {{ tpl.commit_log?.length || 0 }}
                  {{ t("templateMarket.unitCount") }}
                </div>
              </div>
            </el-card>
          </el-col>
        </el-row>
        <el-empty
          v-if="!loading && templates.length === 0"
          :description="t('branchManager.emptyNoBranch')"
        />
      </el-tab-pane>

      <el-tab-pane
        :label="t('templateMarket.tabTrending')"
        name="trending"
        lazy
      >
        <el-table :data="trending" stripe>
          <el-table-column
            prop="name"
            :label="t('templateMarket.labelTemplateName')"
          />
          <el-table-column
            prop="category"
            :label="t('templateMarket.labelCategory')"
            width="120"
          />
          <el-table-column
            prop="adoption_count"
            :label="t('templateMarket.labelAdoptionCount')"
            width="120"
            sortable
          />
          <el-table-column
            :label="t('templateMarket.labelOperation')"
            width="100"
          >
            <template #default="{ row }">
              <el-button
                size="small"
                @click="emit('open-detail', row.branch_id)"
              >
                {{ t("common.view") }}
              </el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-empty
          v-if="trending.length === 0"
          :description="t('templateMarket.emptyNoTrending')"
        />
      </el-tab-pane>

      <el-tab-pane
        :label="t('templateMarket.tabSubscribe')"
        name="subscribe"
        lazy
      >
        <div class="subscribe-section">
          <el-input
            v-model="subscribeCategory"
            :placeholder="t('templateMarket.placeholderSubscribe')"
            style="width: 300px; margin-right: 12px"
          >
            <template #append>
              <el-button @click="subscribe">
                {{ t("templateMarket.btnSubscribe") }}
              </el-button>
            </template>
          </el-input>
          <el-tag
            v-for="cat in subscriptions"
            :key="cat"
            closable
            style="margin: 4px"
            @close="subscriptions = subscriptions.filter((c) => c !== cat)"
          >
            {{ cat }}
          </el-tag>
          <el-empty
            v-if="subscriptions.length === 0"
            :description="t('templateMarket.emptyNoSubscribe')"
          />
        </div>
      </el-tab-pane>

      <el-tab-pane :label="t('templateMarket.tabPublish')" name="publish" lazy>
        <el-form
          :model="publishForm"
          label-width="100px"
          style="max-width: 500px"
        >
          <el-form-item :label="t('templateMarket.labelBranchId')">
            <el-input
              v-model="publishForm.branch_id"
              :placeholder="t('templateMarket.placeholderBranchId')"
            />
          </el-form-item>
          <el-form-item :label="t('templateMarket.labelTemplateName')">
            <el-input
              v-model="publishForm.name"
              :placeholder="t('templateMarket.placeholderTemplateName')"
            />
          </el-form-item>
          <el-form-item :label="t('templateMarket.labelCategory')">
            <el-select v-model="publishForm.category">
              <el-option
                :label="t('templateMarket.categoryGeneral')"
                value="general"
              />
              <el-option
                :label="t('templateMarket.categoryAutomotive')"
                value="automotive"
              />
              <el-option
                :label="t('templateMarket.categoryAerospace')"
                value="aerospace"
              />
              <el-option
                :label="t('templateMarket.categoryMold')"
                value="mold"
              />
              <el-option
                :label="t('templateMarket.categorySteel45')"
                value="steel_45"
              />
              <el-option
                :label="t('templateMarket.categoryAluminum')"
                value="aluminum"
              />
              <el-option
                :label="t('templateMarket.categoryTitanium')"
                value="titanium"
              />
            </el-select>
          </el-form-item>
          <el-form-item :label="t('templateMarket.labelDescription')">
            <el-input
              v-model="publishForm.description"
              type="textarea"
              :rows="3"
            />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="publishTemplate">
              {{ t("templateMarket.btnPublish") }}
            </el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.template-card {
  cursor: pointer;
  margin-bottom: 16px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.template-name {
  font-weight: 600;
  font-size: 16px;
}
.template-meta {
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.8;
}
.loading {
  text-align: center;
  padding: 40px;
  color: var(--text-tertiary);
}
.subscribe-section {
  padding: 20px 0;
}
</style>
