/**
 * FlywheelDashboard.vue 组件测试（p4-6d）
 *
 * 覆盖范围：
 *   1. 组件挂载与基础渲染（页面标题、健康标签、刷新按钮、onMounted 调用 refreshAll）
 *   2. 错误提示横幅（渲染 / 关闭清除）
 *   3. 概览 Tab（空状态 / 8 个指标卡片 / 周报摘要）
 *   4. 反馈 Tab（4 个反馈统计卡片 / 指标定义表格）
 *   5. 模型热更新 Tab（筛选栏 / 活跃部署表格 / 全部部署表格 / 筛选交互）
 *   6. 指标历史 Tab（天数选择器 / 当前指标 / 历史指标表格 / 天数切换）
 *   7. 顶部交互（刷新按钮 / 周报重新生成）
 *
 * 对应 ADR-005 阶段 4 验收标准（前端飞轮看板接入真实数据）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createRouter, createMemoryHistory } from "vue-router";
import type {
  FlywheelStatus,
  FlywheelMetricPoint,
  FlywheelWeeklyReport,
  MetricDefinition,
  DeploymentRecord,
} from "@/stores/flywheel";

// Mock: vue-i18n（useI18n composition API）
vi.mock("vue-i18n", () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      if (params) {
        let result = key;
        Object.entries(params).forEach(([k, v]) => {
          result = result.replace(`{${k}}`, String(v));
        });
        return result;
      }
      return key;
    },
  }),
}));

// Mock: @element-plus/icons-vue
vi.mock("@element-plus/icons-vue", () => ({
  Refresh: { name: "Refresh", template: '<i class="icon-refresh" />' },
  // SnapshotWorkbench（实验快照 Tab）使用
  Plus: { name: "Plus", template: '<i class="icon-plus" />' },
}));

// Mock: element-plus（组件存根）
vi.mock("element-plus", () => ({
  ElMessage: {
    success: vi.fn(),
    warning: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  },
  ElMessageBox: { confirm: vi.fn(() => Promise.resolve("confirm")) },
  // v-loading 指令（AutoImport/ElementPlusResolver 自动注入）
  ElLoadingDirective: { mounted: vi.fn(), unmounted: vi.fn() },
  ElTabs: {
    template: '<div class="el-tabs"><slot /></div>',
    props: ["modelValue", "type"],
    emits: ["update:modelValue"],
  },
  ElTabPane: {
    template: '<div class="el-tab-pane" :data-name="name"><slot /></div>',
    props: ["label", "name"],
  },
  ElButton: {
    template:
      "<button class=\"el-button\" :class=\"{ 'el-button--primary': type === 'primary', 'el-button--danger': type === 'danger', 'is-loading': loading }\" @click=\"$emit('click')\"><slot /><slot name=\"icon\" /></button>",
    props: ["type", "size", "loading", "icon", "link"],
    emits: ["click"],
  },
  ElTag: {
    template: '<span class="el-tag" :data-type="type"><slot /></span>',
    props: ["type", "size", "effect"],
  },
  ElAlert: {
    template:
      '<div class="el-alert"><span class="el-alert__title">{{ title }}</span><button class="el-alert__closebtn" @click="$emit(\'close\')" /></div>',
    props: ["title", "type", "showIcon", "closable"],
    emits: ["close"],
  },
  ElEmpty: {
    template: '<div class="el-empty">{{ description }}</div>',
    props: ["description", "imageSize"],
  },
  ElRow: {
    template: '<div class="el-row"><slot /></div>',
    props: ["gutter"],
  },
  ElCol: {
    template: '<div class="el-col"><slot /></div>',
    props: ["span"],
  },
  ElCard: {
    template:
      '<div class="el-card"><div class="el-card__header"><slot name="header" /></div><div class="el-card__body"><slot /></div></div>',
    props: ["shadow"],
  },
  ElDescriptions: {
    template: '<div class="el-descriptions"><slot /></div>',
    props: ["column", "border"],
  },
  ElDescriptionsItem: {
    template:
      '<div class="el-descriptions-item"><span class="el-descriptions-item__label">{{ label }}</span><span class="el-descriptions-item__content"><slot /></span></div>',
    props: ["label"],
  },
  ElTable: {
    template: '<table class="el-table"><slot /></table>',
    props: ["data", "size", "stripe", "maxHeight", "emptyText"],
  },
  ElTableColumn: {
    // slot 提供 row 默认值：真实 el-table 会传行数据，mock 里防止
    // 模板 #default="{ row }" 解构 undefined（row.status 崩溃）
    template: '<td class="el-table-column"><slot :row="row" /></td>',
    props: ["prop", "label", "width", "showOverflowTooltip"],
    data: () => ({ row: {} }),
  },
  ElInput: {
    template:
      '<input class="el-input" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" @change="$emit(\'change\')" />',
    props: ["modelValue", "type", "rows", "placeholder", "size", "clearable"],
    emits: ["update:modelValue", "change"],
  },
  ElSelect: {
    template:
      '<select class="el-select" :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value); $emit(\'change\')"><slot /></select>',
    props: ["modelValue", "placeholder", "size", "clearable"],
    emits: ["update:modelValue", "change"],
  },
  ElOption: {
    template: '<option class="el-option" :value="value">{{ label }}</option>',
    props: ["value", "label"],
  },
}));

// Mock: @/stores/flywheel（Pinia store 完整 mock）
const mockFlywheelStore = vi.hoisted(() => ({
  // State
  status: null as FlywheelStatus | null,
  currentMetrics: null as FlywheelMetricPoint | null,
  historicalMetrics: [] as FlywheelMetricPoint[],
  metricsPeriodDays: 7,
  weeklyReport: null as FlywheelWeeklyReport | null,
  metricDefinitions: [] as MetricDefinition[],
  deployments: [] as DeploymentRecord[],
  loading: false,
  metricsLoading: false,
  reportLoading: false,
  definitionsLoading: false,
  deploymentsLoading: false,
  error: null as string | null,

  // Computed（作为 getter，访问时实时计算）
  get healthTagType(): "success" | "warning" | "danger" | "info" {
    const s = this.status?.status;
    if (s === "healthy") return "success";
    if (s === "warning") return "warning";
    if (s === "critical") return "danger";
    return "info";
  },
  get healthStatusLabel(): string {
    const map: Record<string, string> = {
      healthy: "健康",
      warning: "警告",
      critical: "严重",
      unknown: "未知",
    };
    return map[this.status?.status ?? "unknown"] ?? "未知";
  },
  get feedbackStats() {
    return {
      dataVolume: this.status?.data_volume ?? 0,
      adoptionRate: this.status?.adoption_rate ?? 0,
      feedbackDelay: this.status?.feedback_delay ?? 0,
      healthScore: this.status?.health_score ?? 0,
    };
  },
  get activeDeployments(): DeploymentRecord[] {
    return this.deployments.filter(
      (d) => d.status === "observing" || d.status === "deploying",
    );
  },
  get promotedDeployments(): DeploymentRecord[] {
    return this.deployments.filter((d) => d.status === "promoted");
  },
  get anyLoading(): boolean {
    return (
      this.loading ||
      this.metricsLoading ||
      this.reportLoading ||
      this.definitionsLoading ||
      this.deploymentsLoading
    );
  },

  // Actions
  refreshAll: vi.fn(() => Promise.resolve()),
  fetchStatus: vi.fn(() => Promise.resolve()),
  fetchMetrics: vi.fn(() => Promise.resolve()),
  fetchWeeklyReport: vi.fn(() => Promise.resolve()),
  fetchDefinitions: vi.fn(() => Promise.resolve()),
  fetchDeployments: vi.fn(() => Promise.resolve()),

  // Helpers
  formatTime: (ts: string | null | undefined) =>
    ts ? new Date(ts).toLocaleString("zh-CN") : "-",
  formatPercent: (v: number | null | undefined, d = 1) =>
    v == null || Number.isNaN(v) ? "-" : `${v.toFixed(d)}%`,
  formatNumber: (v: number | null | undefined) =>
    v == null || Number.isNaN(v) ? "-" : v.toLocaleString("zh-CN"),
}));

vi.mock("@/stores/flywheel", () => ({
  useFlywheelStore: () => mockFlywheelStore,
}));

import FlywheelDashboard from "@/components/FlywheelDashboard.vue";

// 测试数据构造器
function makeStatus(overrides: Partial<FlywheelStatus> = {}): FlywheelStatus {
  return {
    status: "healthy",
    data_volume: 1000,
    model_quality: 92.5,
    adoption_rate: 15.3,
    uncertainty_mean: 0.18,
    feedback_delay: 5.2,
    health_score: 88.0,
    timestamp: "2026-07-13T10:00:00Z",
    ...overrides,
  };
}

function makeDeployment(
  overrides: Partial<DeploymentRecord> = {},
): DeploymentRecord {
  return {
    deployment_id: "dep-001",
    model_name: "ltc-chatter",
    new_model_uri: "model://ltc-chatter-v3",
    baseline_model_uri: "model://ltc-chatter-v2",
    status: "observing",
    canary_ratio: 0.1,
    observation_hours: 24,
    rollback_on_failure: true,
    rollback_metric_drop: 0.05,
    eval_metric: "f1",
    eval_metrics: { f1: 0.92 },
    baseline_metrics: { f1: 0.88 },
    canary_metrics: null,
    decision: null,
    reason: null,
    created_at: "2026-07-13T09:00:00Z",
    updated_at: "2026-07-13T09:30:00Z",
    metadata: null,
    ...overrides,
  };
}

function makeMetricPoint(
  overrides: Partial<FlywheelMetricPoint> = {},
): FlywheelMetricPoint {
  return {
    timestamp: "2026-07-13T10:00:00Z",
    data_volume: 1000,
    model_quality: 92.5,
    adoption_rate: 15.3,
    uncertainty_mean: 0.18,
    feedback_delay: 5.2,
    ...overrides,
  };
}

function makeDefinition(
  overrides: Partial<MetricDefinition> = {},
): MetricDefinition {
  return {
    name: "data_volume",
    description: "加工记录总数",
    unit: "条",
    range: "≥ 0",
    calculation: "COUNT(*) FROM machining_records",
    ...overrides,
  };
}

// 测试主体
describe("FlywheelDashboard.vue", () => {
  let pinia: ReturnType<typeof createPinia>;
  let router: ReturnType<typeof createRouter>;

  beforeEach(() => {
    setActivePinia((pinia = createPinia()));
    router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: { template: "<div/>" } }],
    });

    // 重置 mock 状态
    mockFlywheelStore.status = null;
    mockFlywheelStore.currentMetrics = null;
    mockFlywheelStore.historicalMetrics = [];
    mockFlywheelStore.metricsPeriodDays = 7;
    mockFlywheelStore.weeklyReport = null;
    mockFlywheelStore.metricDefinitions = [];
    mockFlywheelStore.deployments = [];
    mockFlywheelStore.loading = false;
    mockFlywheelStore.metricsLoading = false;
    mockFlywheelStore.reportLoading = false;
    mockFlywheelStore.definitionsLoading = false;
    mockFlywheelStore.deploymentsLoading = false;
    mockFlywheelStore.error = null;

    mockFlywheelStore.refreshAll.mockResolvedValue(undefined);
    mockFlywheelStore.fetchStatus.mockResolvedValue(undefined);
    mockFlywheelStore.fetchMetrics.mockResolvedValue(undefined);
    mockFlywheelStore.fetchWeeklyReport.mockResolvedValue(undefined);
    mockFlywheelStore.fetchDefinitions.mockResolvedValue(undefined);
    mockFlywheelStore.fetchDeployments.mockResolvedValue(undefined);

    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  const mountDashboard = (options = {}) => {
    return mount(FlywheelDashboard, {
      global: {
        plugins: [pinia, router],
        ...options,
      },
    });
  };

  // 1. 组件挂载与基础渲染
  describe("基础渲染", () => {
    it("组件能正确挂载", async () => {
      const wrapper = mountDashboard();
      await flushPromises();
      expect(wrapper.exists()).toBe(true);
      expect(wrapper.find(".flywheel-dashboard-page").exists()).toBe(true);
    });

    it("渲染页面标题与副标题", async () => {
      const wrapper = mountDashboard();
      await flushPromises();
      expect(wrapper.find(".page-header__title h1").text()).toBe(
        "flywheel.pageTitle",
      );
      expect(wrapper.find(".page-header__subtitle").text()).toBe(
        "flywheel.pageSubtitle",
      );
    });

    it("渲染刷新按钮", async () => {
      const wrapper = mountDashboard();
      await flushPromises();
      const actions = wrapper.find(".page-header__actions");
      expect(actions.exists()).toBe(true);
      const buttons = actions.findAll("button");
      expect(buttons.length).toBeGreaterThanOrEqual(1);
    });

    it("挂载时调用 refreshAll", async () => {
      mountDashboard();
      await flushPromises();
      expect(mockFlywheelStore.refreshAll).toHaveBeenCalled();
    });

    it("挂载时 refreshAll 传入默认天数 7", async () => {
      mountDashboard();
      await flushPromises();
      expect(mockFlywheelStore.refreshAll).toHaveBeenCalledWith(7);
    });

    it("status 为 null 时不渲染健康标签", async () => {
      const wrapper = mountDashboard();
      await flushPromises();
      const tags = wrapper.find(".page-header__actions").findAll(".el-tag");
      expect(tags.length).toBe(0);
    });

    it("status 存在时渲染健康标签", async () => {
      mockFlywheelStore.status = makeStatus({ status: "healthy" });
      const wrapper = mountDashboard();
      await flushPromises();
      const tag = wrapper.find(".page-header__actions .el-tag");
      expect(tag.exists()).toBe(true);
      expect(tag.text()).toContain("flywheel.healthLabel");
      expect(tag.text()).toContain("健康");
    });

    it("健康状态为 warning 时标签 type 为 warning", async () => {
      mockFlywheelStore.status = makeStatus({ status: "warning" });
      const wrapper = mountDashboard();
      await flushPromises();
      const tag = wrapper.find(".page-header__actions .el-tag");
      expect(tag.attributes("data-type")).toBe("warning");
    });

    it("健康状态为 critical 时标签 type 为 danger", async () => {
      mockFlywheelStore.status = makeStatus({ status: "critical" });
      const wrapper = mountDashboard();
      await flushPromises();
      const tag = wrapper.find(".page-header__actions .el-tag");
      expect(tag.attributes("data-type")).toBe("danger");
    });
  });

  // 2. 错误提示横幅
  describe("错误提示", () => {
    it("store.error 存在时渲染错误提示横幅", async () => {
      mockFlywheelStore.error = "获取飞轮状态失败";
      const wrapper = mountDashboard();
      await flushPromises();
      const banner = wrapper.find(".error-banner");
      expect(banner.exists()).toBe(true);
      expect(banner.text()).toContain("获取飞轮状态失败");
    });

    it("store.error 为 null 时不渲染错误提示横幅", async () => {
      const wrapper = mountDashboard();
      await flushPromises();
      expect(wrapper.find(".error-banner").exists()).toBe(false);
    });

    it("点击关闭按钮清除错误", async () => {
      mockFlywheelStore.error = "获取飞轮状态失败";
      const wrapper = mountDashboard();
      await flushPromises();
      const closeBtn = wrapper.find(".error-banner .el-alert__closebtn");
      expect(closeBtn.exists()).toBe(true);
      await closeBtn.trigger("click");
      await flushPromises();
      expect(mockFlywheelStore.error).toBeNull();
    });
  });

  // 3. 概览 Tab
  describe("概览 Tab", () => {
    it("status 为空且非加载中时渲染空状态", async () => {
      const wrapper = mountDashboard();
      await flushPromises();
      const overviewTab = wrapper.find('[data-name="overview"]');
      expect(overviewTab.exists()).toBe(true);
      expect(overviewTab.find(".el-empty").exists()).toBe(true);
    });

    it("status 存在时渲染指标卡片", async () => {
      mockFlywheelStore.status = makeStatus();
      const wrapper = mountDashboard();
      await flushPromises();
      const overviewTab = wrapper.find('[data-name="overview"]');
      const cards = overviewTab.findAll(".metric-card");
      // 2 行 × 4 列 = 8 个指标卡片
      expect(cards.length).toBe(8);
    });

    it("指标卡片包含健康分数", async () => {
      mockFlywheelStore.status = makeStatus({ health_score: 88.0 });
      const wrapper = mountDashboard();
      await flushPromises();
      const overviewTab = wrapper.find('[data-name="overview"]');
      const healthCard = overviewTab.find(".metric-card--health");
      expect(healthCard.exists()).toBe(true);
      expect(healthCard.text()).toContain("88");
    });

    it("指标卡片包含数据量", async () => {
      mockFlywheelStore.status = makeStatus({ data_volume: 12345 });
      const wrapper = mountDashboard();
      await flushPromises();
      const overviewTab = wrapper.find('[data-name="overview"]');
      const cards = overviewTab.findAll(".metric-card");
      const dataVolumeCard = cards.find((c) =>
        c.text().includes("flywheel.metricDataVolume"),
      );
      expect(dataVolumeCard).toBeDefined();
      expect(dataVolumeCard?.text()).toContain("12,345");
    });

    it("渲染周报摘要卡片", async () => {
      mockFlywheelStore.status = makeStatus();
      const wrapper = mountDashboard();
      await flushPromises();
      const overviewTab = wrapper.find('[data-name="overview"]');
      const reportCard = overviewTab.findAll(".section-card");
      expect(reportCard.length).toBeGreaterThanOrEqual(1);
    });

    it("weeklyReport 为空时渲染周报空状态", async () => {
      mockFlywheelStore.status = makeStatus();
      const wrapper = mountDashboard();
      await flushPromises();
      const overviewTab = wrapper.find('[data-name="overview"]');
      const reportSection = overviewTab.find(".section-card");
      expect(reportSection.find(".el-empty").exists()).toBe(true);
    });

    it("weeklyReport 存在时渲染周报内容", async () => {
      mockFlywheelStore.status = makeStatus();
      mockFlywheelStore.weeklyReport = {
        report_type: "weekly",
        generated_at: "2026-07-13T10:00:00Z",
        period: { start: "2026-07-06", end: "2026-07-13" },
        current_metrics: {},
        trends: {},
        summary: { health_score: 88 },
      };
      const wrapper = mountDashboard();
      await flushPromises();
      const overviewTab = wrapper.find('[data-name="overview"]');
      const reportBody = overviewTab.find(".report-body");
      expect(reportBody.exists()).toBe(true);
      expect(reportBody.find(".el-descriptions").exists()).toBe(true);
    });

    it("周报重新生成按钮点击调用 fetchWeeklyReport", async () => {
      mockFlywheelStore.status = makeStatus();
      const wrapper = mountDashboard();
      await flushPromises();
      mockFlywheelStore.fetchWeeklyReport.mockClear();
      // 找到周报卡片中的重新生成按钮（link 样式按钮）
      const overviewTab = wrapper.find('[data-name="overview"]');
      const sectionCard = overviewTab.find(".section-card");
      const headerBtn = sectionCard.find(".el-card__header button");
      expect(headerBtn.exists()).toBe(true);
      await headerBtn.trigger("click");
      await flushPromises();
      expect(mockFlywheelStore.fetchWeeklyReport).toHaveBeenCalledWith(false);
    });
  });

  // 4. 反馈 Tab
  describe("反馈 Tab", () => {
    it("渲染 4 个反馈统计卡片", async () => {
      mockFlywheelStore.status = makeStatus();
      const wrapper = mountDashboard();
      await flushPromises();
      const feedbackTab = wrapper.find('[data-name="feedback"]');
      expect(feedbackTab.exists()).toBe(true);
      const cards = feedbackTab.findAll(".metric-card");
      expect(cards.length).toBe(4);
    });

    it("反馈统计卡片包含数据量", async () => {
      mockFlywheelStore.status = makeStatus({ data_volume: 500 });
      const wrapper = mountDashboard();
      await flushPromises();
      const feedbackTab = wrapper.find('[data-name="feedback"]');
      const cards = feedbackTab.findAll(".metric-card");
      const dataVolumeCard = cards.find((c) =>
        c.text().includes("flywheel.feedbackDataVolume"),
      );
      expect(dataVolumeCard).toBeDefined();
      expect(dataVolumeCard?.text()).toContain("500");
    });

    it("渲染指标定义表格", async () => {
      const wrapper = mountDashboard();
      await flushPromises();
      const feedbackTab = wrapper.find('[data-name="feedback"]');
      const table = feedbackTab.find(".el-table");
      expect(table.exists()).toBe(true);
    });

    it("metricDefinitions 为空时表格仍渲染", async () => {
      mockFlywheelStore.metricDefinitions = [];
      const wrapper = mountDashboard();
      await flushPromises();
      const feedbackTab = wrapper.find('[data-name="feedback"]');
      expect(feedbackTab.find(".el-table").exists()).toBe(true);
    });

    it("metricDefinitions 有数据时渲染定义行", async () => {
      mockFlywheelStore.metricDefinitions = [
        makeDefinition({ name: "data_volume", description: "加工记录总数" }),
        makeDefinition({
          name: "model_quality",
          description: "模型质量",
          unit: "%",
        }),
      ];
      const wrapper = mountDashboard();
      await flushPromises();
      const feedbackTab = wrapper.find('[data-name="feedback"]');
      const columns = feedbackTab.findAll(".el-table-column");
      // 5 列：name / description / unit / range / calculation
      expect(columns.length).toBe(5);
    });

    it("点击重新加载按钮调用 fetchDefinitions", async () => {
      const wrapper = mountDashboard();
      await flushPromises();
      mockFlywheelStore.fetchDefinitions.mockClear();
      const feedbackTab = wrapper.find('[data-name="feedback"]');
      const sectionCard = feedbackTab.find(".section-card");
      const headerBtn = sectionCard.find(".el-card__header button");
      expect(headerBtn.exists()).toBe(true);
      await headerBtn.trigger("click");
      await flushPromises();
      expect(mockFlywheelStore.fetchDefinitions).toHaveBeenCalled();
    });
  });

  // 5. 模型热更新 Tab
  describe("模型热更新 Tab", () => {
    it("渲染筛选栏（输入框 + 下拉框 + 搜索按钮 + 重置按钮）", async () => {
      const wrapper = mountDashboard();
      await flushPromises();
      const modelsTab = wrapper.find('[data-name="models"]');
      expect(modelsTab.exists()).toBe(true);
      const filterBar = modelsTab.find(".filter-bar");
      expect(filterBar.exists()).toBe(true);
      expect(filterBar.find(".el-input").exists()).toBe(true);
      expect(filterBar.find(".el-select").exists()).toBe(true);
      const buttons = filterBar.findAll("button");
      expect(buttons.length).toBe(2); // 搜索 + 重置
    });

    it("部署列表为空时渲染活跃部署空状态", async () => {
      mockFlywheelStore.deployments = [];
      const wrapper = mountDashboard();
      await flushPromises();
      const modelsTab = wrapper.find('[data-name="models"]');
      const cards = modelsTab.findAll(".section-card");
      expect(cards.length).toBeGreaterThanOrEqual(2);
      // 第一个卡片（活跃部署）应包含 el-empty
      expect(cards[0].find(".el-empty").exists()).toBe(true);
    });

    it("有活跃部署时渲染活跃部署表格", async () => {
      mockFlywheelStore.deployments = [
        makeDeployment({
          deployment_id: "dep-001",
          status: "observing",
        }),
        makeDeployment({
          deployment_id: "dep-002",
          status: "deploying",
        }),
      ];
      const wrapper = mountDashboard();
      await flushPromises();
      const modelsTab = wrapper.find('[data-name="models"]');
      const cards = modelsTab.findAll(".section-card");
      // 活跃部署卡片应包含 el-table（而非 el-empty）
      expect(cards[0].find(".el-table").exists()).toBe(true);
      expect(cards[0].find(".el-empty").exists()).toBe(false);
    });

    it("活跃部署仅包含 observing 和 deploying 状态", async () => {
      mockFlywheelStore.deployments = [
        makeDeployment({ deployment_id: "dep-active", status: "observing" }),
        makeDeployment({ deployment_id: "dep-done", status: "promoted" }),
        makeDeployment({ deployment_id: "dep-fail", status: "failed" }),
      ];
      mountDashboard();
      await flushPromises();
      // activeDeployments getter 只返回 observing/deploying
      expect(mockFlywheelStore.activeDeployments.length).toBe(1);
      expect(mockFlywheelStore.activeDeployments[0].deployment_id).toBe(
        "dep-active",
      );
    });

    it("全部部署表格在有数据时渲染", async () => {
      mockFlywheelStore.deployments = [
        makeDeployment({ deployment_id: "dep-001" }),
        makeDeployment({ deployment_id: "dep-002" }),
      ];
      const wrapper = mountDashboard();
      await flushPromises();
      const modelsTab = wrapper.find('[data-name="models"]');
      const cards = modelsTab.findAll(".section-card");
      // 第二个卡片（全部部署）应包含 el-table
      expect(cards[1].find(".el-table").exists()).toBe(true);
    });

    it("全部部署表格为空时渲染空状态", async () => {
      mockFlywheelStore.deployments = [];
      const wrapper = mountDashboard();
      await flushPromises();
      const modelsTab = wrapper.find('[data-name="models"]');
      const cards = modelsTab.findAll(".section-card");
      expect(cards[1].find(".el-empty").exists()).toBe(true);
    });

    it("筛选输入框 change 触发 fetchDeployments", async () => {
      const wrapper = mountDashboard();
      await flushPromises();
      mockFlywheelStore.fetchDeployments.mockClear();
      const modelsTab = wrapper.find('[data-name="models"]');
      const input = modelsTab.find(".filter-bar .el-input");
      await input.trigger("change");
      await flushPromises();
      expect(mockFlywheelStore.fetchDeployments).toHaveBeenCalled();
    });

    it("状态下拉框 change 触发 fetchDeployments", async () => {
      const wrapper = mountDashboard();
      await flushPromises();
      mockFlywheelStore.fetchDeployments.mockClear();
      const modelsTab = wrapper.find('[data-name="models"]');
      const select = modelsTab.find(".filter-bar .el-select");
      await select.trigger("change");
      await flushPromises();
      expect(mockFlywheelStore.fetchDeployments).toHaveBeenCalled();
    });

    it("点击搜索按钮触发 fetchDeployments", async () => {
      const wrapper = mountDashboard();
      await flushPromises();
      mockFlywheelStore.fetchDeployments.mockClear();
      const modelsTab = wrapper.find('[data-name="models"]');
      const buttons = modelsTab.findAll(".filter-bar button");
      const searchBtn = buttons[0]; // 第一个按钮是搜索
      await searchBtn.trigger("click");
      await flushPromises();
      expect(mockFlywheelStore.fetchDeployments).toHaveBeenCalled();
    });

    it("点击重置按钮触发 fetchDeployments", async () => {
      const wrapper = mountDashboard();
      await flushPromises();
      mockFlywheelStore.fetchDeployments.mockClear();
      const modelsTab = wrapper.find('[data-name="models"]');
      const buttons = modelsTab.findAll(".filter-bar button");
      const resetBtn = buttons[1]; // 第二个按钮是重置
      await resetBtn.trigger("click");
      await flushPromises();
      expect(mockFlywheelStore.fetchDeployments).toHaveBeenCalled();
    });

    it("状态下拉框包含 5 种部署状态选项", async () => {
      const wrapper = mountDashboard();
      await flushPromises();
      const modelsTab = wrapper.find('[data-name="models"]');
      const options = modelsTab.findAll(".filter-bar .el-option");
      expect(options.length).toBe(5);
      const values = options.map((o) => o.attributes("value"));
      expect(values).toEqual(
        expect.arrayContaining([
          "deploying",
          "observing",
          "promoted",
          "rolled_back",
          "failed",
        ]),
      );
    });
  });

  // 6. 指标历史 Tab
  describe("指标历史 Tab", () => {
    it("渲染天数选择器", async () => {
      const wrapper = mountDashboard();
      await flushPromises();
      const metricsTab = wrapper.find('[data-name="metrics"]');
      expect(metricsTab.exists()).toBe(true);
      const filterBar = metricsTab.find(".filter-bar");
      expect(filterBar.exists()).toBe(true);
      expect(filterBar.find(".el-select").exists()).toBe(true);
    });

    it("天数选择器包含 5 个选项（1/7/14/30/90）", async () => {
      const wrapper = mountDashboard();
      await flushPromises();
      const metricsTab = wrapper.find('[data-name="metrics"]');
      const options = metricsTab.findAll(".filter-bar .el-option");
      expect(options.length).toBe(5);
      const values = options.map((o) => o.attributes("value"));
      expect(values).toEqual(
        expect.arrayContaining(["1", "7", "14", "30", "90"]),
      );
    });

    it("currentMetrics 为空时渲染当前指标空状态", async () => {
      mockFlywheelStore.currentMetrics = null;
      const wrapper = mountDashboard();
      await flushPromises();
      const metricsTab = wrapper.find('[data-name="metrics"]');
      const cards = metricsTab.findAll(".section-card");
      expect(cards[0].find(".el-empty").exists()).toBe(true);
    });

    it("currentMetrics 存在时渲染当前指标描述列表", async () => {
      mockFlywheelStore.currentMetrics = makeMetricPoint();
      const wrapper = mountDashboard();
      await flushPromises();
      const metricsTab = wrapper.find('[data-name="metrics"]');
      const cards = metricsTab.findAll(".section-card");
      expect(cards[0].find(".el-descriptions").exists()).toBe(true);
      expect(cards[0].find(".el-empty").exists()).toBe(false);
    });

    it("historicalMetrics 为空时渲染历史指标空状态", async () => {
      mockFlywheelStore.historicalMetrics = [];
      const wrapper = mountDashboard();
      await flushPromises();
      const metricsTab = wrapper.find('[data-name="metrics"]');
      const cards = metricsTab.findAll(".section-card");
      // 第二个卡片是历史指标
      expect(cards[1].find(".el-empty").exists()).toBe(true);
    });

    it("historicalMetrics 有数据时渲染历史指标表格", async () => {
      mockFlywheelStore.historicalMetrics = [
        makeMetricPoint({
          timestamp: "2026-07-12T10:00:00Z",
          data_volume: 900,
        }),
        makeMetricPoint({
          timestamp: "2026-07-11T10:00:00Z",
          data_volume: 800,
        }),
      ];
      const wrapper = mountDashboard();
      await flushPromises();
      const metricsTab = wrapper.find('[data-name="metrics"]');
      const cards = metricsTab.findAll(".section-card");
      expect(cards[1].find(".el-table").exists()).toBe(true);
      expect(cards[1].find(".el-empty").exists()).toBe(false);
    });

    it("天数选择器 change 触发 fetchMetrics", async () => {
      const wrapper = mountDashboard();
      await flushPromises();
      mockFlywheelStore.fetchMetrics.mockClear();
      const metricsTab = wrapper.find('[data-name="metrics"]');
      const select = metricsTab.find(".filter-bar .el-select");
      await select.trigger("change");
      await flushPromises();
      expect(mockFlywheelStore.fetchMetrics).toHaveBeenCalled();
    });

    it("点击指标刷新按钮调用 fetchMetrics", async () => {
      const wrapper = mountDashboard();
      await flushPromises();
      mockFlywheelStore.fetchMetrics.mockClear();
      const metricsTab = wrapper.find('[data-name="metrics"]');
      const refreshBtn = metricsTab.find(".filter-bar button");
      expect(refreshBtn.exists()).toBe(true);
      await refreshBtn.trigger("click");
      await flushPromises();
      expect(mockFlywheelStore.fetchMetrics).toHaveBeenCalled();
    });
  });

  // 7. 顶部交互
  describe("顶部交互", () => {
    it("点击刷新按钮调用 refreshAll", async () => {
      const wrapper = mountDashboard();
      await flushPromises();
      mockFlywheelStore.refreshAll.mockClear();
      const refreshBtn = wrapper.find(".page-header__actions button");
      expect(refreshBtn.exists()).toBe(true);
      await refreshBtn.trigger("click");
      await flushPromises();
      expect(mockFlywheelStore.refreshAll).toHaveBeenCalledWith(7);
    });

    it("anyLoading 为 true 时刷新按钮处于 loading 状态", async () => {
      mockFlywheelStore.loading = true;
      const wrapper = mountDashboard();
      await flushPromises();
      const refreshBtn = wrapper.find(".page-header__actions button");
      // ElButton mock 通过 is-loading class 表达 loading 状态
      expect(refreshBtn.classes("is-loading")).toBe(true);
    });

    it("anyLoading 为 false 时刷新按钮非 loading 状态", async () => {
      const wrapper = mountDashboard();
      await flushPromises();
      const refreshBtn = wrapper.find(".page-header__actions button");
      expect(refreshBtn.classes("is-loading")).toBe(false);
    });
  });
});
